"""Triage agent for recurring issues: evidence in, a decision and a message out.

The agent is deliberately bounded:

    1. It reads only an EVIDENCE PACK built from the recurrence statistics
       (partnerflow/recurrence.py), never the raw database.
    2. It must pick a RUNBOOK (partnerflow/runbooks.py). It cannot invent a
       diagnosis for a failure nobody has catalogued; it says so instead.
    3. It ACTS only when the statistics say the episode is real (flagged), and
       only with the actions the runbook allows (runbooks.AUTO_ACTIONS). It can
       replay webhooks. It cannot touch a fault switch or a release.
    4. Every decision, including "watch and wait", is written to triage_log with
       the evidence it saw, so a person can audit it.

Two decision engines produce the same shape:

    rules   deterministic, needs nothing, always available
    llm     Claude writes the summary and the partner message from the evidence
            (needs ANTHROPIC_API_KEY in the API's environment); the rules engine
            still checks owner and action, and takes over if the model's answer
            is malformed or breaks a guardrail

Command line:  python triage.py            (see the script at the project root)
API:           POST /lab/triage, GET /lab/triage, PUT /lab/triage/auto
"""

import json
import os
from datetime import datetime, timezone

import httpx
import pandas as pd

from partnerflow import analytics, config, db, faults, recurrence, runbooks

DECISION_KEYS = ["status", "class", "owner", "severity", "blast_radius", "summary", "diagnosis",
                 "checks", "internal_action", "auto_action", "partner_message", "confidence",
                 "mode", "runbook"]

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


# =============================================================================
# Gathering: the same statistics the dashboard shows
# =============================================================================

def gather(gap_seconds: float = recurrence.GAP_SECONDS,
           clean_requests: int = recurrence.CLEAN_REQUESTS) -> dict:
    """Load the logs and compute events, episodes and the registry."""
    requests = analytics.load_requests()
    webhooks = db.list_webhook_deliveries(limit=100_000)
    events = recurrence.events_frame(requests, webhooks)
    episodes = recurrence.build_episodes(events, gap_seconds, clean_requests)
    registry = recurrence.build_registry(events, episodes, gap_seconds, clean_requests)
    return {"events": events, "episodes": episodes, "registry": registry,
            "gap_seconds": gap_seconds, "clean_requests": clean_requests}


# =============================================================================
# The evidence pack: everything the agent is allowed to reason from
# =============================================================================

def evidence_pack(row: dict, episodes: pd.DataFrame, events: pd.DataFrame,
                  registry: pd.DataFrame) -> dict:
    """A JSON-safe summary of one fingerprint. No raw rows, no secrets."""
    partner = config.PARTNER_BY_ID.get(row["partner_id"], {})
    mine = episodes[episodes["fingerprint"] == row["fingerprint"]].sort_values("episode")
    history = [{
        "episode": int(e["episode"]),
        "start": _iso(e["start"]), "end": _iso(e["end"]),
        "occurrences": int(e["occurrences"]), "exposed_requests": int(e["exposed_requests"]),
        "share": round(float(e["share"]), 4),
        "run_labels": list(e["run_labels"]), "release_versions": list(e["release_versions"]),
    } for _, e in mine.iterrows()]

    # Did other partners show the same failure at the same time? A release or a
    # dependency hits everyone; a key or a payload problem hits one partner.
    same_failure = registry[(registry["status_code"] == row["status_code"])
                            & (registry["failure_reason"] == row["failure_reason"])
                            & (registry["partner_id"] != row["partner_id"])]
    others = [{"partner_id": o["partner_id"], "open": bool(o["open"]), "flagged": bool(o["flagged"]),
               "latest_occurrences": int(o["latest_occurrences"])}
              for _, o in same_failure.iterrows()]

    latest_window = events[(events["partner_id"] == row["partner_id"])
                           & (events["timestamp"] >= row["latest_start"])
                           & (events["timestamp"] <= row["latest_end"])]
    failures = latest_window[(latest_window["status_code"] == row["status_code"])
                             & (latest_window["reason_key"] == row["failure_reason"])]

    pack = {
        "fingerprint": row["fingerprint"],
        "partner_id": row["partner_id"],
        "partner_name": partner.get("name", row["partner_id"]),
        "status_code": int(row["status_code"]),
        "failure_reason": row["failure_reason"],
        "endpoints": list(row["endpoints"]),
        "runbook": row["runbook"],
        "episodes": history,
        "recurring": bool(row["recurring"]),
        "median_gap_seconds": _num(row["median_gap_seconds"]),
        "open": bool(row["open"]),
        "latest": {
            "start": _iso(row["latest_start"]), "end": _iso(row["latest_end"]),
            "occurrences": int(row["latest_occurrences"]),
            "exposed_requests": int(row["latest_exposed"]),
            "share": round(float(row["latest_share"]), 4),
            "baseline_rate": round(float(row["baseline_rate"]), 6),
            "baseline_requests": int(row["baseline_clean_requests"]),
            "baseline_failures": int(row["baseline_failures"]),
            "tail_probability": _num(row["tail_p"]),
            "flagged": bool(row["flagged"]),
            "posterior_share": {"mean": round(float(row["posterior_mean"]), 4),
                                "low": round(float(row["posterior_low"]), 4),
                                "high": round(float(row["posterior_high"]), 4)},
            "trend": row["trend"],
            "trend_difference": _num(row["trend_difference"]),
            "trend_p": _num(row["trend_p"]),
            "run_labels": list(row["latest_run_labels"]),
            "release_versions": list(row["latest_release_versions"]),
            "products": dict(row["latest_products"]),
            "customers_affected": int(row["latest_customers"]),
            "sample_request_ids": list(row["latest_request_ids"]),
        },
        "other_partners_same_failure": others,
        "latency": _latency(events, row),
    }
    if row["failure_reason"] == "validation_error":
        pack["top_invalid_fields"] = _top_invalid_fields(row)
    if row["failure_reason"] == "receiver_unavailable":
        pack["webhooks_still_failed"] = len(db.list_webhook_deliveries(limit=100_000, failed_only=True))
    pack["exception_types"] = _exception_types(row) if row["failure_reason"] == "server_exception" else []
    return pack


def _latency(events: pd.DataFrame, row: dict) -> dict:
    """p50/p95 for the partner's API traffic during the latest episode vs outside it."""
    partner = events[(events["partner_id"] == row["partner_id"]) & (events["source"] == "api")]
    if partner.empty:
        return {}
    during = partner[(partner["timestamp"] >= row["latest_start"]) & (partner["timestamp"] <= row["latest_end"])]
    outside = partner[(partner["timestamp"] < row["latest_start"]) | (partner["timestamp"] > row["latest_end"])]

    def summary(frame):
        if frame.empty or frame["latency_ms"].dropna().empty:
            return None
        return {"p50_ms": round(float(frame["latency_ms"].median()), 1),
                "p95_ms": round(float(frame["latency_ms"].quantile(0.95)), 1), "requests": int(len(frame))}
    return {"during_episode": summary(during), "outside_episode": summary(outside)}


def _top_invalid_fields(row: dict) -> list[dict]:
    frame = analytics.load_requests()
    if "invalid_fields" not in frame.columns:
        return []
    hits = frame[(frame["partner_id"] == row["partner_id"]) & (frame["failure_reason"] == "validation_error")]
    counts: dict[str, int] = {}
    for fields in hits["invalid_fields"].dropna():
        for name in (fields if isinstance(fields, list) else []):
            counts[name] = counts.get(name, 0) + 1
    return [{"field": k, "count": v} for k, v in sorted(counts.items(), key=lambda kv: -kv[1])[:5]]


def _exception_types(row: dict) -> list[dict]:
    frame = analytics.load_requests()
    if "exception_type" not in frame.columns:
        return []
    hits = frame[(frame["partner_id"] == row["partner_id"]) & (frame["failure_reason"] == "server_exception")]
    counts = hits["exception_type"].dropna().astype(str).value_counts().head(3)
    return [{"exception": k, "count": int(v)} for k, v in counts.items()]


def _iso(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return pd.Timestamp(value).isoformat(timespec="milliseconds")


def _num(value):
    try:
        return None if pd.isna(value) else float(value)
    except TypeError:
        return None


# =============================================================================
# Deciding: rules
# =============================================================================

def decide_rules(evidence: dict) -> dict:
    """Deterministic decision from the evidence pack and the runbook."""
    book = runbooks.RUNBOOKS.get(evidence["runbook"]) or runbooks.lookup(
        evidence["status_code"], evidence["failure_reason"])
    latest = evidence["latest"]
    others_now = [o["partner_id"] for o in evidence["other_partners_same_failure"] if o["flagged"]]
    blast_radius = "all partners" if others_now else "one partner"
    if book["class"] in ("server", "release-regression") and not others_now:
        blast_radius = "one partner so far"

    if latest["flagged"]:
        status = "act"
        confidence = "high" if (latest["tail_probability"] or 1) < 1e-6 else "medium"
    else:
        status = "watch"
        confidence = "low"

    summary = _summary(evidence, book, status, blast_radius)
    decision = {
        "status": status,
        "class": book["class"],
        "owner": book["owner"],
        "severity": book["severity"] if status == "act" else "low",
        "blast_radius": blast_radius,
        "summary": summary,
        "diagnosis": book["diagnosis"],
        "checks": list(book["checks"]),
        "internal_action": book["internal_action"],
        "auto_action": book["auto_action"] if status == "act" else None,
        "partner_message": fill_message(book["partner_message"], evidence) if status == "act" else "",
        "confidence": confidence,
        "mode": "rules",
        "runbook": evidence["runbook"],
    }
    return guardrails(decision, evidence)


def _summary(evidence: dict, book: dict, status: str, blast_radius: str) -> str:
    latest = evidence["latest"]
    episodes = len(evidence["episodes"])
    when = f"episode {episodes}" if episodes > 1 else "first episode"
    rate = f"{latest['occurrences']} of {latest['exposed_requests']} ({latest['share'] * 100:.0f}%)"
    if status == "act":
        verdict = ("a real shift in what the partner sends, not a fault on our side"
                   if book["class"] == "business-rule" else "so this is real")
        head = (f"{evidence['partner_name']}: {latest['occurrences']} × {evidence['status_code']} "
                f"{evidence['failure_reason']} on {', '.join(evidence['endpoints'])}, {when}. "
                f"{rate} of exposed traffic against a baseline of {latest['baseline_rate'] * 100:.2f}% "
                f"({latest['baseline_failures']} in {latest['baseline_requests']} earlier requests): "
                f"tail probability {latest['tail_probability']:.2g}, {verdict}. ")
        tail = f"Owner: {book['owner']}. Blast radius: {blast_radius}."
        if evidence["recurring"] and evidence["median_gap_seconds"]:
            tail += (f" It has come back {episodes} times; the gap between episodes is "
                     f"{_duration(evidence['median_gap_seconds'])}.")
        if latest["trend"] == "worse":
            tail += " The latest episode is worse than the earlier ones."
        return head + tail
    if latest["tail_probability"] is None:
        return (f"{evidence['partner_name']}: {rate} on {', '.join(evidence['endpoints'])}, {when}. "
                "No earlier traffic to compare against, so there is no baseline yet. Watching.")
    return (f"{evidence['partner_name']}: {rate} on {', '.join(evidence['endpoints'])}, {when}. "
            f"Tail probability {latest['tail_probability']:.2g} against a baseline of "
            f"{latest['baseline_rate'] * 100:.1f}% with {latest['occurrences']} occurrence(s): "
            f"not distinct from the baseline, or too few to call. Watching.")


def _duration(seconds: float) -> str:
    if seconds < 90:
        return "under a minute (lab time)"
    if seconds < 5400:
        return f"{seconds / 60:.0f} minutes"
    if seconds < 172800:
        return f"{seconds / 3600:.1f} hours"
    return f"{seconds / 86400:.1f} days"


def fill_message(template: str, evidence: dict) -> str:
    latest = evidence["latest"]
    start = latest["start"][11:16] if latest["start"] else "recently"
    products = ", ".join(f"{k} ({v})" for k, v in latest["products"].items()) or "n/a"
    request_id = latest["sample_request_ids"][0] if latest["sample_request_ids"] else "n/a"
    text = template.format(
        partner=evidence["partner_name"], count=latest["occurrences"], n=latest["exposed_requests"],
        share=f"{latest['share'] * 100:.0f}%", endpoints=", ".join(evidence["endpoints"]),
        window=f"{start} UTC", request_id=request_id, products=products, episodes=len(evidence["episodes"]))
    if evidence.get("top_invalid_fields"):
        fields = ", ".join(f"{f['field']} ({f['count']})" for f in evidence["top_invalid_fields"])
        text = text.replace("The response body lists the fields; the most frequent ones are below.",
                            f"The fields most often at fault: {fields}.")
    if evidence["recurring"] and len(evidence["episodes"]) >= 2:
        text += (f"\n\nThis is the {_ordinal(len(evidence['episodes']))} time this pattern has appeared, "
                 "so we would like to agree a permanent fix rather than clear it again.")
    return text


def _ordinal(n: int) -> str:
    return {1: "first", 2: "second", 3: "third", 4: "fourth", 5: "fifth"}.get(n, f"{n}th")


def guardrails(decision: dict, evidence: dict) -> dict:
    """Rules no engine may break, applied to every decision."""
    book = runbooks.RUNBOOKS.get(evidence["runbook"]) or runbooks.lookup(
        evidence["status_code"], evidence["failure_reason"])
    notes = []
    if evidence["status_code"] >= 500 and decision.get("owner") != "us":
        decision["owner"] = "us"
        notes.append("owner forced to 'us': a 5xx is never the partner's fault")
    allowed = {book["auto_action"], None}
    if decision.get("auto_action") not in allowed:
        notes.append(f"auto_action '{decision.get('auto_action')}' not allowed by the runbook; removed")
        decision["auto_action"] = None
    if not evidence["latest"]["flagged"] and decision.get("status") == "act":
        decision["status"] = "watch"
        decision["auto_action"] = None
        notes.append("status forced to 'watch': the episode is not statistically distinct yet")
    if decision.get("status") not in ("act", "watch"):
        decision["status"] = "watch"
        decision["auto_action"] = None
    for key in DECISION_KEYS:
        decision.setdefault(key, None)
    if notes:
        decision["guardrail_notes"] = notes
    return decision


# =============================================================================
# Deciding: Claude writes the summary and the message, inside the same fence
# =============================================================================

SYSTEM_PROMPT = """You are the triage agent for a lending API that partners integrate with.
You receive an EVIDENCE PACK (statistics about one recurring failure fingerprint) and the
RUNBOOK that applies. Write the decision as one JSON object with exactly these keys:
status ("act" or "watch"), class, owner ("us", "partner" or "both"), severity ("high",
"medium" or "low"), blast_radius, summary (two or three sentences for an engineer, with the
actual numbers), diagnosis, checks (list of strings), internal_action, auto_action (the
runbook's auto_action or null), partner_message (a short, direct message to the partner's
engineers: what we saw with numbers, whose side it is on, what happens next; no apology
unless it is our fault), confidence ("high", "medium" or "low").
Rules: use "act" only when latest.flagged is true. A 5xx is always owner "us". Never
promise a fix time you cannot know. Do not invent facts that are not in the evidence.
Return only the JSON object."""


def decide_llm(evidence: dict, model: str | None = None, timeout: float = 60) -> dict:
    """Ask Claude for the decision; fall back to the rules on any problem."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    fallback = decide_rules(evidence)
    if not api_key:
        fallback["llm_note"] = "ANTHROPIC_API_KEY is not set; used the rules engine."
        return fallback
    book = runbooks.RUNBOOKS.get(evidence["runbook"]) or runbooks.lookup(
        evidence["status_code"], evidence["failure_reason"])
    user = ("EVIDENCE PACK:\n" + json.dumps(evidence, indent=1) +
            "\n\nRUNBOOK:\n" + json.dumps(book, indent=1))
    body = {"model": model or config.TRIAGE_MODEL, "max_tokens": 1500, "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user}]}
    headers = {"x-api-key": api_key, "anthropic-version": ANTHROPIC_VERSION,
               "content-type": "application/json"}
    try:
        response = httpx.post(ANTHROPIC_URL, json=body, headers=headers, timeout=timeout)
        response.raise_for_status()
        text = "".join(block.get("text", "") for block in response.json()["content"]
                       if block.get("type") == "text")
        start, end = text.find("{"), text.rfind("}")
        decision = json.loads(text[start:end + 1])
        if not isinstance(decision, dict):
            raise ValueError("not an object")
    except (httpx.HTTPError, ValueError, KeyError, json.JSONDecodeError) as exc:
        fallback["llm_note"] = f"LLM call failed ({type(exc).__name__}); used the rules engine."
        return fallback
    decision["mode"] = "llm"
    decision["runbook"] = evidence["runbook"]
    decision["model"] = body["model"]
    if not isinstance(decision.get("checks"), list):
        decision["checks"] = list(book["checks"])
    for key in ("summary", "diagnosis", "internal_action", "partner_message"):
        if not isinstance(decision.get(key), str):
            decision[key] = fallback[key]
    return guardrails(decision, evidence)


def decide(evidence: dict, mode: str = "rules") -> dict:
    if mode == "llm":
        return decide_llm(evidence)
    return decide_rules(evidence)


# =============================================================================
# Acting: only what the runbook allows, only when the decision says "act"
# =============================================================================

def execute(decision: dict, evidence: dict, lab: dict, dry_run: bool = False) -> list[dict]:
    """Carry out the decision's auto_action. Returns the actions taken, with results."""
    action = decision.get("auto_action")
    if decision.get("status") != "act" or not action:
        return []
    if action not in runbooks.AUTO_ACTIONS:
        return [{"action": action, "result": "refused: not an allowed action"}]
    if dry_run:
        return [{"action": action, "result": "dry run: not executed",
                 "description": runbooks.AUTO_ACTIONS[action]}]
    if action == "redeliver_webhooks":
        outcome = faults.redeliver_failed_webhooks(lab)
        note = ("all delivered" if outcome["still_failed"] == 0 else
                "receiver still failing; the agent replays again on its next pass")
        return [{"action": action, "result": "executed", "note": note, **outcome}]
    if action == "attach_backoff_guidance":
        decision["partner_message"] += ("\n\nGuidance: retry on 429 and 5xx only, wait "
                                        "Retry-After or 2^n seconds plus jitter, cap at 5 attempts, "
                                        "and send an Idempotency-Key header so a retry cannot "
                                        "create a second application.")
        return [{"action": action, "result": "executed"}]
    # open_incident and page_oncall are simulated: in a real estate these call
    # the incident tool and the paging service.
    return [{"action": action, "result": "simulated", "description": runbooks.AUTO_ACTIONS[action]}]


# =============================================================================
# One pass of the agent
# =============================================================================

def run(gap_seconds: float = recurrence.GAP_SECONDS, clean_requests: int = recurrence.CLEAN_REQUESTS,
        mode: str = "rules", dry_run: bool = False, force: bool = False,
        fingerprint: str | None = None, only_open: bool = True) -> dict:
    """Look at every fingerprint, decide, act, log. Idempotent per episode.

    By default only OPEN episodes (still happening in log time) are triaged, and
    an episode the agent has already acted on is skipped. `force` triages the
    chosen fingerprint(s) again regardless. A `watch` decision never blocks a
    later look, because the episode may grow into something real.
    """
    data = gather(gap_seconds, clean_requests)
    registry, episodes, events = data["registry"], data["episodes"], data["events"]
    lab = db.get_lab_config()
    done = db.triaged_episodes()
    triaged, skipped = [], []
    for row in recurrence.to_records(registry) if len(registry) else []:
        if fingerprint and row["fingerprint"] != fingerprint:
            continue
        key = (row["fingerprint"], row["latest_start"])
        if not force:
            if only_open and not row["open"]:
                skipped.append({"fingerprint": row["fingerprint"], "why": "not open"})
                continue
            if key in done["acted"]:
                skipped.append({"fingerprint": row["fingerprint"], "why": "already acted on this episode"})
                continue
            if (*key, row["latest_occurrences"]) in done["watched"]:
                skipped.append({"fingerprint": row["fingerprint"], "why": "watching; episode unchanged"})
                continue
        # The evidence pack needs the typed registry row (timestamps as Timestamps).
        typed = registry[registry["fingerprint"] == row["fingerprint"]].iloc[0].to_dict()
        evidence = evidence_pack(typed, episodes, events, registry)
        decision = decide(evidence, mode)
        actions = execute(decision, evidence, lab, dry_run)
        entry = {
            "fingerprint": row["fingerprint"], "partner_id": row["partner_id"],
            "status_code": row["status_code"], "failure_reason": row["failure_reason"],
            "episode": row["episodes"], "episode_start": row["latest_start"],
            "occurrences": row["latest_occurrences"],
            "mode": decision.get("mode", mode), "status": decision["status"], "dry_run": dry_run,
            "decision": decision, "actions": actions,
        }
        entry["id"] = db.insert_triage_entry(entry)
        entry["evidence"] = evidence
        triaged.append(entry)
    followups = [] if dry_run else webhook_followup(lab, done, registry)
    return {"ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "mode": mode,
            "dry_run": dry_run, "fingerprints": int(len(registry)), "triaged": triaged, "skipped": skipped,
            "followups": followups, "llm_available": config.llm_available()}


def webhook_followup(lab: dict, done: dict, registry: pd.DataFrame) -> list[dict]:
    """Keep replaying failed webhooks on every pass after the agent has acted on a
    receiver_unavailable fingerprint, and log only when deliveries recover.

    A receiver that is down stays down for a while; the first pass acts, later
    passes quietly retry until the replay succeeds, then one line is logged."""
    if not len(registry):
        return []
    acted = {fp for fp, _ in done["acted"]}
    webhook_rows = registry[(registry["failure_reason"] == "receiver_unavailable")
                            & registry["fingerprint"].isin(acted)]
    if webhook_rows.empty or not db.list_webhook_deliveries(limit=1, failed_only=True):
        return []
    outcome = faults.redeliver_failed_webhooks(lab)
    if outcome["recovered"] == 0:
        return [{"action": "redeliver_webhooks", "result": "retried, receiver still failing", **outcome}]
    entries = []
    for _, row in webhook_rows.iterrows():
        entry = {
            "fingerprint": row["fingerprint"], "partner_id": row["partner_id"],
            "status_code": int(row["status_code"]), "failure_reason": row["failure_reason"],
            "episode": int(row["episodes"]), "episode_start": _iso(row["latest_start"]),
            "occurrences": int(row["latest_occurrences"]), "mode": "rules", "status": "followup",
            "dry_run": False,
            "decision": {"status": "followup", "summary": f"Replayed failed webhooks: {outcome['recovered']} "
                         f"delivered, {outcome['still_failed']} still failing."},
            "actions": [{"action": "redeliver_webhooks", "result": "executed", **outcome}],
        }
        entry["id"] = db.insert_triage_entry(entry)
        entries.append(entry)
    return entries
