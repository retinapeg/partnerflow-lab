"""PartnerFlow Lab API.

Synthetic data. Fictional lending simulation. Not affiliated with iwoca.

Run it on its own:   uvicorn main:app --port 8010
Interactive docs:    http://127.0.0.1:8010/docs

How a request travels through this file:

    client -> Uvicorn -> record_request (middleware)
                           -> require_partner (auth, rate limit, latency)
                           -> Pydantic validates the JSON body
                           -> the endpoint function (rules, SQLite)
                         <- record_request writes one row to request_log
"""

import asyncio
import logging
import re
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from starlette.exceptions import HTTPException as StarletteHTTPException

from partnerflow import config, db, faults, recurrence, rules, schemas, triage
from partnerflow.metrics import build_metrics

logger = logging.getLogger("partnerflow")


async def auto_triage_loop(stop: asyncio.Event) -> None:
    """Background timer: run the triage agent when the lab has it switched on.

    The setting is read from SQLite on every tick, so the dashboard can switch
    it on and off without restarting the API. The agent runs in a worker thread
    so it never blocks requests.
    """
    while not stop.is_set():
        settings = db.get_lab_config().get("auto_triage", config.DEFAULT_AUTO_TRIAGE)
        enabled = bool(settings.get("enabled"))
        if enabled:
            try:
                await asyncio.to_thread(triage.run, mode=settings.get("mode", "rules"))
            except Exception:
                logger.exception("Automatic triage failed")
        wait = max(5, int(settings.get("interval_seconds", 30))) if enabled else 5
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait)
        except asyncio.TimeoutError:
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the server starts: make sure the database exists, and
    start the automatic-triage timer (idle until switched on)."""
    db.init_db()
    stop = asyncio.Event()
    timer = asyncio.create_task(auto_triage_loop(stop))
    try:
        yield
    finally:
        stop.set()
        await timer


app = FastAPI(
    title="PartnerFlow Lab API",
    version="1.1.1",
    description=(
        f"**{config.BANNER}**\n\n"
        "Partner endpoints need an `X-API-Key` header. Demo keys: "
        "`demo-key-alder-0001`, `demo-key-birch-0002`, `demo-key-cedar-0003`. "
        "Click **Authorize** to set one for this page.\n\n"
        "`/lab/...` endpoints control the simulation (release, faults, reset) "
        "and let you inspect recorded requests."
    ),
    lifespan=lifespan,
)

# Shown on error responses in /docs.
ERRORS = {
    401: {"model": schemas.ErrorResponse, "description": "Missing, unknown or revoked API key"},
    404: {"model": schemas.ErrorResponse, "description": "Resource not found"},
    409: {"model": schemas.ErrorResponse, "description": "The request conflicts with the current state"},
    422: {"model": schemas.ErrorResponse, "description": "Invalid JSON body, or a business rule refused it"},
    429: {"model": schemas.ErrorResponse, "description": "Rate limit exceeded"},
    500: {"model": schemas.ErrorResponse, "description": "Unhandled server error"},
}


# =============================================================================
# Errors: one shape for every failure
# =============================================================================

class ApiError(Exception):
    """Raise this anywhere to send a well-formed error response."""

    def __init__(self, status_code: int, code: str, message: str,
                 details: list[dict] | None = None, headers: dict | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers


def error_json(request: Request, status_code: int, code: str, message: str,
               details: list[dict] | None = None, headers: dict | None = None) -> JSONResponse:
    """Build the error response and note the reason for the request log."""
    request.state.failure_reason = code
    body = {"error": {"code": code, "message": message,
                      "request_id": request.state.request_id, "details": details}}
    return JSONResponse(status_code=status_code, content=body, headers=headers)


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError):
    """Errors this application raises on purpose (auth, not found, rules, rate limit)."""
    return error_json(request, exc.status_code, exc.code, exc.message, exc.details, exc.headers)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    """Errors raised by Pydantic/FastAPI before the endpoint function runs."""
    details = []
    for error in exc.errors():
        # error["loc"] looks like ("body", "amount"). The submitted value is
        # left out on purpose: the log must not store what the caller sent.
        field = ".".join(str(part) for part in error["loc"] if part != "body") or "body"
        details.append({"field": field, "problem": error["msg"], "type": error["type"]})
    request.state.meta["invalid_fields"] = [d["field"] for d in details]
    request.state.meta["validation_problems"] = [d["type"] for d in details]
    return error_json(request, 422, "validation_error",
                      "The request did not match the expected format.", details)


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    """Errors raised by the framework itself, e.g. 404 for a URL with no route."""
    codes = {404: "route_not_found", 405: "method_not_allowed"}
    code = codes.get(exc.status_code, f"http_{exc.status_code}")
    return error_json(request, exc.status_code, code, str(exc.detail))


# =============================================================================
# Middleware: wraps every request, measures it and records it
# =============================================================================

def clean_label(value: str | None) -> str | None:
    """Keep the optional X-Lab-Run label short and harmless."""
    if not value:
        return None
    return re.sub(r"[^A-Za-z0-9_.\- ]", "", value)[:40] or None


@app.middleware("http")
async def record_request(request: Request, call_next):
    request_id = uuid.uuid4().hex[:16]
    lab = db.get_lab_config()   # read fresh on every request: no caching

    # request.state is a scratchpad shared by everything handling this request.
    request.state.request_id = request_id
    request.state.lab = lab
    request.state.partner_id = None
    request.state.failure_reason = None
    request.state.meta = {}

    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        # Nothing handled this exception, so it is a genuine server error.
        # The stack trace goes to the server console; the log row gets a summary.
        logger.exception("Unhandled exception, request_id=%s", request_id)
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        request.state.meta["exception_type"] = type(exc).__name__
        request.state.meta["raised_in"] = f"{Path(frame.filename).name}:{frame.lineno} {frame.name}()"
        response = error_json(request, 500, "server_exception",
                              "The server hit an unexpected error. Quote the request_id.")
    latency_ms = (time.perf_counter() - started) * 1000

    response.headers["X-Request-ID"] = request_id

    if not request.url.path.startswith(config.UNLOGGED_PREFIXES):
        route = request.scope.get("route")
        meta = request.state.meta
        journey = request.headers.get("x-lab-journey", "")
        if journey.isdigit():
            meta["journey"] = int(journey)
        db.insert_request_log({
            "timestamp": db.now_iso(),
            "request_id": request_id,
            "partner_id": request.state.partner_id,
            "method": request.method,
            "endpoint": route.path if route else "(no matching route)",
            "path": request.url.path,
            "status_code": response.status_code,
            "latency_ms": round(latency_ms, 2),
            "failure_reason": request.state.failure_reason,
            "release_version": config.release_version(lab["release"], lab["hotfix"]),
            "run_label": clean_label(request.headers.get("x-lab-run")),
            "metadata": meta,
        })
    return response


# =============================================================================
# Authentication (plus the faults that act per partner)
# =============================================================================

api_key_header = APIKeyHeader(
    name="X-API-Key", auto_error=False,
    description="A demo partner key, e.g. demo-key-alder-0001",
)


def require_partner(request: Request, api_key: str | None = Depends(api_key_header)) -> dict:
    """Work out which partner is calling. Runs before the endpoint function."""
    lab = request.state.lab

    if api_key is None:
        raise ApiError(401, "missing_api_key", "Send your partner key in the X-API-Key header.")
    partner = config.PARTNER_BY_KEY.get(api_key)
    if partner is None:
        raise ApiError(401, "invalid_api_key", "This API key is not recognised.")

    # From here on the log knows the partner. The key itself is never stored.
    request.state.partner_id = partner["id"]

    if faults.key_is_revoked(lab, partner["id"]):
        raise ApiError(401, "api_key_revoked", "This API key has been revoked.")

    retry_after = faults.rate_limit_retry_after(lab, partner["id"])
    if retry_after is not None:
        raise ApiError(429, "rate_limited", f"Too many requests. Retry in {retry_after} seconds.",
                       headers={"Retry-After": str(retry_after)})

    delay_ms = faults.injected_delay_ms(lab, partner["id"])
    if delay_ms:
        request.state.meta["injected_delay_ms"] = delay_ms

    return partner


# =============================================================================
# Operations endpoints
# =============================================================================

@app.get("/health", response_model=schemas.Health, tags=["operations"])
def health():
    """Is the API up, and can it reach its database?"""
    lab = db.get_lab_config()
    return {
        "status": "ok",
        "release_version": config.release_version(lab["release"], lab["hotfix"]),
        "database": "ok",
    }


@app.get("/partners", response_model=schemas.PartnerList, tags=["operations"])
def list_partners():
    """The three simulated partners and the product limits. No API keys."""
    return {
        "partners": [{"id": p["id"], "name": p["name"], "channel": p["channel"]}
                     for p in config.PARTNERS],
        "products": [{"product": name, **limits} for name, limits in config.PRODUCTS.items()],
    }


@app.get("/metrics", response_model=schemas.Metrics, tags=["operations"])
def metrics(request: Request,
            run_label: str | None = Query(default=None, description="Only count one traffic run.")):
    """Request counts, error rates and latency, computed from the request log.

    `run_label` narrows the request numbers. The `applications` block always
    covers every stored application.
    """
    return build_metrics(request.state.lab, run_label)


# =============================================================================
# Partner API: customers
# =============================================================================

@app.post("/customers", response_model=schemas.Customer, status_code=201,
          responses={k: ERRORS[k] for k in (401, 422, 429)}, tags=["partner API"])
def create_customer(body: schemas.CustomerCreate, request: Request,
                    partner: dict = Depends(require_partner)):
    """Register a fictional business so it can apply."""
    customer = db.insert_customer(partner["id"], body.model_dump())
    request.state.meta["customer_id"] = customer["id"]
    return customer


@app.get("/customers/{customer_id}", response_model=schemas.Customer,
         responses={k: ERRORS[k] for k in (401, 404, 422, 429)}, tags=["partner API"])
def get_customer(customer_id: int, request: Request, partner: dict = Depends(require_partner)):
    """Fetch one of the calling partner's customers."""
    request.state.meta["customer_id"] = customer_id
    customer = db.get_customer(customer_id)
    # Another partner's customer is reported as "not found" as well, so one
    # partner cannot discover which IDs belong to someone else.
    if customer is None or customer["partner_id"] != partner["id"]:
        raise ApiError(404, "customer_not_found", f"No customer {customer_id} for this partner.")
    return customer


# =============================================================================
# Partner API: applications
# =============================================================================

@app.post("/applications", response_model=schemas.Application, status_code=201,
          responses={k: ERRORS[k] for k in (401, 404, 422, 429, 500)}, tags=["partner API"])
def create_application(body: schemas.ApplicationCreate, request: Request,
                       partner: dict = Depends(require_partner)):
    """Submit an application for an existing customer."""
    lab = request.state.lab
    # Non-sensitive facts about the request, kept for investigations.
    request.state.meta.update(customer_id=body.customer_id, product=body.product,
                              amount=body.amount, term_months=body.term_months)

    # 1. The customer must exist and belong to this partner.
    customer = db.get_customer(body.customer_id)
    if customer is None or customer["partner_id"] != partner["id"]:
        raise ApiError(404, "customer_not_found", f"No customer {body.customer_id} for this partner.")

    # 2. Business rules for the active release (see partnerflow/rules.py).
    violation = rules.check_application(body.product, body.amount, lab)
    if violation:
        request.state.meta["rule_id"] = violation.rule_id
        raise ApiError(422, violation.code, violation.message,
                       details=[{"rule_id": violation.rule_id}])

    # 3. Simulated downstream call. With the server_error fault on, this raises.
    faults.call_scoring_service(lab, partner["id"])

    # 4. Toy decision, then store the application.
    status, reason = rules.toy_decision(body.amount, customer["annual_turnover"],
                                        customer["trading_months"])
    application = db.insert_application(
        partner["id"], body.model_dump(), status, reason,
        config.release_version(lab["release"], lab["hotfix"]),
    )
    request.state.meta.update(application_id=application["id"], decision=status)

    # 5. Tell the partner by (simulated) webhook. A failed webhook does not
    #    fail the request: the application has already been saved.
    request.state.meta["webhook"] = faults.deliver_webhook(lab, application)
    return application


@app.get("/applications/{application_id}", response_model=schemas.Application,
         responses={k: ERRORS[k] for k in (401, 404, 422, 429)}, tags=["partner API"])
def get_application(application_id: int, request: Request,
                    partner: dict = Depends(require_partner)):
    """Fetch one of the calling partner's applications."""
    request.state.meta["application_id"] = application_id
    application = db.get_application(application_id)
    if application is None or application["partner_id"] != partner["id"]:
        raise ApiError(404, "application_not_found",
                       f"No application {application_id} for this partner.")
    return application


# =============================================================================
# Lab controls: release, hotfix, faults, reset, request inspection
# These are local simulation controls, so they do not ask for a key.
# =============================================================================

def lab_state() -> dict:
    lab = db.get_lab_config()
    version = config.release_version(lab["release"], lab["hotfix"])
    return {**lab, "version": version, "release_notes": config.RELEASE_NOTES,
            "fault_info": config.FAULT_INFO}


@app.get("/lab/config", tags=["lab controls"])
def get_lab_config():
    """Current release, hotfix, rule band and fault settings."""
    return lab_state()


@app.post("/lab/release", tags=["lab controls"])
def set_release(body: schemas.ReleaseChange):
    """Deploy release A (v1.0.0) or release B (v1.1.0). Always removes the hotfix."""
    db.set_lab_config("release", body.release)
    db.set_lab_config("hotfix", False)
    return lab_state()


@app.post("/lab/hotfix", responses={409: ERRORS[409]}, tags=["lab controls"])
def set_hotfix(body: schemas.HotfixChange):
    """Apply or remove the targeted fix (v1.1.1). Only meaningful on release B."""
    if body.enabled and db.get_lab_config()["release"] != "B":
        raise ApiError(409, "hotfix_requires_release_b",
                       "The hotfix patches release B. Activate release B first.")
    db.set_lab_config("hotfix", body.enabled)
    return lab_state()


@app.put("/lab/r204-band", tags=["lab controls"])
def set_r204_band(body: schemas.BandChange):
    """Change the amount band that rule R-204 looks at."""
    if body.min_amount > body.max_amount:
        raise ApiError(422, "invalid_band", "min_amount must not be greater than max_amount.")
    db.set_lab_config("r204_band", [body.min_amount, body.max_amount])
    return lab_state()


@app.put("/lab/faults/{name}", responses={404: ERRORS[404]}, tags=["lab controls"])
def set_fault(name: str, body: schemas.FaultChange):
    """Switch a fault on or off, or change its settings."""
    lab = db.get_lab_config()
    if name not in lab["faults"]:
        raise ApiError(404, "unknown_fault", f"Faults: {', '.join(lab['faults'])}.")
    changes = body.model_dump(exclude_none=True)
    unsupported = [key for key in changes if key not in lab["faults"][name]]
    if unsupported:
        raise ApiError(422, "setting_not_supported",
                       f"Fault '{name}' has no setting: {', '.join(unsupported)}.")
    lab["faults"][name].update(changes)
    db.set_lab_config("faults", lab["faults"])
    faults.reset_runtime_state()   # counters and rate-limit windows start again
    return lab_state()


@app.post("/lab/reset", tags=["lab controls"])
def reset_lab(body: schemas.ResetRequest | None = None):
    """Delete all recorded data. By default also restores release A with no faults."""
    db.reset_data(keep_config=bool(body and body.keep_config))
    faults.reset_runtime_state()
    return lab_state()


@app.get("/lab/requests", response_model=list[schemas.RequestLogEntry], tags=["lab controls"])
def list_requests(limit: int = Query(default=50, ge=1, le=5000),
                  partner_id: str | None = None, run_label: str | None = None,
                  failure_reason: str | None = None, failures_only: bool = False):
    """Recorded requests, newest first."""
    return db.list_request_log(limit, partner_id, run_label, failure_reason, failures_only)


@app.get("/lab/requests/{request_id}", response_model=schemas.RequestLogEntry,
         responses={404: ERRORS[404]}, tags=["lab controls"])
def get_request(request_id: str):
    """Everything recorded about one request. Use the X-Request-ID you were given."""
    entry = db.get_request_log_entry(request_id)
    if entry is None:
        raise ApiError(404, "request_not_found", f"No recorded request with id {request_id}.")
    return entry


@app.get("/lab/webhooks", response_model=list[schemas.WebhookDelivery], tags=["lab controls"])
def list_webhooks(limit: int = Query(default=50, ge=1, le=5000), failed_only: bool = False):
    """Recorded webhook deliveries, newest first."""
    return db.list_webhook_deliveries(limit, failed_only)


@app.post("/lab/webhooks/redeliver", tags=["lab controls"])
def redeliver_webhooks():
    """Retry every failed webhook delivery using the current fault settings."""
    return faults.redeliver_failed_webhooks(db.get_lab_config())


# =============================================================================
# Recurring issues and the triage agent
# =============================================================================

@app.get("/lab/recurring", tags=["recurring issues"])
def recurring_issues(gap_seconds: float = Query(default=recurrence.GAP_SECONDS, ge=1, le=86_400),
                     clean_requests: int = Query(default=recurrence.CLEAN_REQUESTS, ge=1, le=10_000)):
    """Every failure fingerprint with its episodes and recurrence statistics.

    See partnerflow/recurrence.py for the definitions. The same numbers drive
    the dashboard's Recurring issues page and the triage agent.
    """
    data = triage.gather(gap_seconds, clean_requests)
    return {
        "parameters": {"gap_seconds": gap_seconds, "clean_requests": clean_requests,
                       "min_occurrences": recurrence.MIN_OCCURRENCES, "alpha": recurrence.ALPHA},
        "events": int(len(data["events"])),
        "registry": recurrence.to_records(data["registry"]),
        "episodes": recurrence.to_records(data["episodes"].drop(columns=["first_pos", "last_pos"]))
        if len(data["episodes"]) else [],
    }


@app.post("/lab/triage", tags=["recurring issues"])
def run_triage(body: schemas.TriageRequest | None = None):
    """Run the triage agent once: decide, act (within the runbook), log.

    `mode` is "rules" (deterministic) or "llm" (Claude writes the summary and
    message; needs ANTHROPIC_API_KEY on the API, otherwise falls back to rules).
    `dry_run` logs the decision without acting. `force` re-triages even
    episodes already handled, and includes closed ones.
    """
    body = body or schemas.TriageRequest()
    return triage.run(gap_seconds=body.gap_seconds, clean_requests=body.clean_requests,
                      mode=body.mode, dry_run=body.dry_run, force=body.force,
                      fingerprint=body.fingerprint, only_open=not body.force and body.only_open)


@app.get("/lab/triage", tags=["recurring issues"])
def triage_log(limit: int = Query(default=50, ge=1, le=5000), fingerprint: str | None = None):
    """Every decision the agent has made, newest first, with the actions it took."""
    return {"auto_triage": db.get_lab_config().get("auto_triage", config.DEFAULT_AUTO_TRIAGE),
            "llm_available": config.llm_available(), "model": config.TRIAGE_MODEL,
            "entries": db.list_triage_log(limit, fingerprint)}


@app.put("/lab/triage/auto", tags=["recurring issues"])
def set_auto_triage(body: schemas.AutoTriageChange):
    """Switch the automatic timer on or off, or change its interval and mode."""
    settings = dict(db.get_lab_config().get("auto_triage", config.DEFAULT_AUTO_TRIAGE))
    settings.update(body.model_dump(exclude_none=True))
    db.set_lab_config("auto_triage", settings)
    return lab_state()
