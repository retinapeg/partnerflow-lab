"""Settings for PartnerFlow Lab.

Everything in this file is fictional: the partners, the API keys, the products
and the rules. Nothing here comes from a real lender.
"""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"

BANNER = "Synthetic data. Fictional lending simulation. Not affiliated with iwoca."

# --- Where things run -------------------------------------------------------
# Port 8000 is left free on purpose: it is the default for `uvicorn main:app`.
API_HOST = "127.0.0.1"
API_PORT = int(os.environ.get("PARTNERFLOW_API_PORT", "8010"))
DASHBOARD_PORT = int(os.environ.get("PARTNERFLOW_DASHBOARD_PORT", "8510"))


def api_url() -> str:
    """Base URL the dashboard and scripts use to reach the API."""
    return os.environ.get("PARTNERFLOW_API_URL", f"http://{API_HOST}:{API_PORT}")


def db_path() -> Path:
    """Location of the SQLite file.

    Read from the environment on every call so tests can point the app at a
    temporary database without touching the real one.
    """
    return Path(os.environ.get("PARTNERFLOW_DB", str(DATA_DIR / "partnerflow.db")))


# --- The three simulated lending partners -----------------------------------
# The keys are demo values, printed in the README. They are still treated like
# real credentials by the code: they are never written to the request log.
PARTNERS = [
    {
        "id": "alder",
        "name": "Alder Marketplace",
        "channel": "online marketplace",
        "api_key": "demo-key-alder-0001",
    },
    {
        "id": "birch",
        "name": "Birch Books",
        "channel": "accounting software",
        "api_key": "demo-key-birch-0002",
    },
    {
        "id": "cedar",
        "name": "Cedar Pay",
        "channel": "payments provider",
        "api_key": "demo-key-cedar-0003",
    },
]
PARTNER_IDS = [p["id"] for p in PARTNERS]
PARTNER_BY_KEY = {p["api_key"]: p for p in PARTNERS}
PARTNER_BY_ID = {p["id"]: p for p in PARTNERS}

# --- Products and their INTENDED eligibility limits (rule R-101) ------------
# These limits are meant to be there. They are never switched off by the lab.
PRODUCTS = {
    "term_loan": {"min_amount": 1_000, "max_amount": 250_000},
    "credit_line": {"min_amount": 1_000, "max_amount": 100_000},
    "invoice_finance": {"min_amount": 5_000, "max_amount": 150_000},
}

# --- Releases ---------------------------------------------------------------
# Release A is the baseline. Release B adds rule R-204. The hotfix is a patch
# release on top of B. See partnerflow/rules.py for the rule itself.
R204_PRODUCT = "invoice_finance"
R204_DEFAULT_BAND = (25_000, 50_000)

RELEASE_NOTES = {
    "v1.0.0": "Baseline. Product limits (R-101) only.",
    "v1.1.0": (
        "New rule R-204: invoice_finance applications inside the amount band "
        "need manual underwriting, so the API no longer accepts them. "
        "No change intended for term_loan or credit_line."
    ),
    "v1.1.1": (
        "Patch: R-204 now checks the product. It applies to invoice_finance "
        "only, as originally intended."
    ),
}


def release_version(release: str, hotfix: bool) -> str:
    """Translate the lab's two switches into a version string for the logs."""
    if release == "A":
        return "v1.0.0"
    return "v1.1.1" if hotfix else "v1.1.0"


# --- Fault scenarios --------------------------------------------------------
# "server" faults change what the API does. "client" faults change what the
# traffic generator sends, so the API reacts to genuinely bad requests.
DEFAULT_FAULTS = {
    "auth_revoked": {"enabled": False, "partner": "cedar"},
    "server_error": {"enabled": False, "partner": "all", "every_nth": 4},
    "latency": {"enabled": False, "partner": "birch", "delay_ms": 250},
    "rate_limit": {"enabled": False, "partner": "alder", "limit": 20, "window_seconds": 60},
    "webhook_failure": {"enabled": False, "partner": "cedar"},
    "bad_payloads": {"enabled": False, "partner": "birch", "share": 0.3},
    "missing_resources": {"enabled": False, "partner": "birch", "share": 0.3},
}

FAULT_INFO = {
    "auth_revoked": {
        "label": "A. Authentication failure",
        "side": "server",
        "what": "The partner's API key is treated as revoked.",
        "expect": "Every request from that partner returns 401 (api_key_revoked).",
    },
    "bad_payloads": {
        "label": "B. Invalid application payloads",
        "side": "client",
        "what": "A share of the partner's generated applications are malformed "
        "(missing field, wrong type, negative amount, unknown product).",
        "expect": "Those requests return 422 (validation_error) from Pydantic.",
    },
    "missing_resources": {
        "label": "C. Missing customer or application",
        "side": "client",
        "what": "A share of the partner's generated requests point at IDs that do not exist.",
        "expect": "Those requests return 404 (customer_not_found / application_not_found).",
    },
    "server_error": {
        "label": "D. Server-side exception",
        "side": "server",
        "what": "The simulated scoring service raises an exception on every Nth application.",
        "expect": "Those POST /applications calls return 500 (server_exception).",
    },
    "latency": {
        "label": "E. Increased latency",
        "side": "server",
        "what": "Each request from the partner is delayed by a fixed number of milliseconds.",
        "expect": "Status codes stay the same but recorded latency rises.",
    },
    "rate_limit": {
        "label": "F. Simulated rate limiting",
        "side": "server",
        "what": "The partner is limited to N requests per time window.",
        "expect": "Requests over the limit return 429 (rate_limited) with a Retry-After header.",
    },
    "webhook_failure": {
        "label": "H. Webhook delivery failure",
        "side": "server",
        "what": "The partner's simulated webhook receiver answers 503.",
        "expect": "Applications still succeed, but their webhook deliveries are recorded as failed.",
    },
}

# --- Recurring-issue triage (partnerflow/triage.py) ---------------------------
# The agent runs on demand from the dashboard, from `python triage.py`, or on a
# timer inside the API when auto_triage is switched on. "rules" needs nothing;
# "llm" needs ANTHROPIC_API_KEY in the API's environment.
DEFAULT_AUTO_TRIAGE = {"enabled": False, "interval_seconds": 30, "mode": "rules"}
TRIAGE_MODEL = os.environ.get("PARTNERFLOW_TRIAGE_MODEL", "claude-haiku-5-5")


def llm_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


DEFAULT_LAB_CONFIG = {
    "release": "A",
    "hotfix": False,
    "r204_band": list(R204_DEFAULT_BAND),
    "faults": DEFAULT_FAULTS,
    "auto_triage": DEFAULT_AUTO_TRIAGE,
}

# Requests to these paths are not written to the request log. They are
# operational or lab-control calls (the dashboard polls them constantly), and
# counting them would dilute the partner-facing error rate.
UNLOGGED_PREFIXES = ("/health", "/metrics", "/lab", "/docs", "/redoc", "/openapi.json", "/favicon.ico")
