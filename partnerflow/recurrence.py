"""Recurring issues: the same failure coming back, and how sure we are that it has.

Works on the live request log (plus the webhook delivery log). Every failed
request gets a FINGERPRINT:

    (partner, status code, failure reason)      e.g. cedar, 401, api_key_revoked

The endpoints a fingerprint has ever hit are its EXPOSED traffic. An EPISODE is
a run of occurrences of one fingerprint with no quiet gap inside it. A quiet
gap is either:

    * `clean_requests` consecutive exposed requests from that partner that did
      not fail this way (default 10), or
    * `gap_seconds` with no occurrence at all (default 120 s).

The lab runs at demo speed, so two minutes here stands in for the days a real
estate would use. A fingerprint with two or more episodes is RECURRING.

Statistics, per episode, in time order (the monitor only knows the past):

    baseline rate p0   the fingerprint's share of exposed traffic BEFORE the episode, with
                       episodes already recognised as incidents taken out of the history, as
                       a Jeffreys estimate (x + 0.5) / (n + 1). With 300 clean requests and no
                       failures that is 0.17%: "normal" is not exactly zero, we just have not
                       seen it yet. A steady background rate (an intended rule refusing 5% of
                       applications) stays in the baseline, so it is not mistaken for an
                       incident; a real incident is taken out, so its recurrence is not
                       mistaken for the new normal. With no history at all there is no test.
    tail probability   P(X >= x | n, p0), the exact binomial upper tail, as in
                       partnerflow/monitoring.py. Flagged below the same 3-sigma threshold,
                       AND only when the episode has at least `min_occurrences`: one request
                       is an anecdote, three are a pattern.
    posterior          Beta(0.5 + x, 0.5 + n - x) for the share of exposed traffic failing
                       during the episode: a mean and a 95% interval that stay honest when
                       counts are small.

And per fingerprint:

    trend              the latest episode's share against all earlier episodes pooled, a
                       two-proportion z-test (analytics.compare_proportions).
    gap                median time between the starts of consecutive episodes.

Nothing here writes anything. partnerflow/triage.py turns these numbers into a
decision and a message.
"""

import numpy as np
import pandas as pd
from scipy import stats

from partnerflow import runbooks
from partnerflow.analytics import compare_proportions

GAP_SECONDS = 120
CLEAN_REQUESTS = 10
MIN_OCCURRENCES = 3
ALPHA = float(stats.norm.sf(3))      # one-sided 3-sigma tail, 0.00135, as in monitoring.py

WEBHOOK_REQUEST = "webhook application.decided"
UNKNOWN_PARTNER = "(no key)"

EVENT_COLUMNS = ["source", "id", "timestamp", "partner_id", "request", "status_code",
                 "reason_key", "failed", "run_label", "release_version", "request_id",
                 "product", "customer_id", "application_id", "latency_ms"]

EPISODE_COLUMNS = ["fingerprint", "partner_id", "status_code", "failure_reason", "episode",
                   "start", "end", "occurrences", "exposed_requests", "share", "endpoints",
                   "run_labels", "release_versions", "products", "customers_affected",
                   "sample_request_ids", "first_pos", "last_pos", "baseline_requests",
                   "baseline_failures", "baseline_rate", "tail_p", "flagged",
                   "posterior_mean", "posterior_low", "posterior_high"]

REGISTRY_COLUMNS = ["fingerprint", "partner_id", "status_code", "failure_reason", "runbook",
                    "class", "owner", "status", "endpoints", "episodes", "occurrences", "first_seen",
                    "last_seen", "open", "recurring", "median_gap_seconds", "baseline_clean_requests",
                    "baseline_failures", "baseline_rate", "latest_start", "latest_end", "latest_occurrences",
                    "latest_exposed", "latest_share", "tail_p", "flagged", "posterior_mean",
                    "posterior_low", "posterior_high", "trend", "trend_difference", "trend_p",
                    "latest_run_labels", "latest_release_versions", "latest_products",
                    "latest_customers", "latest_request_ids"]


# =============================================================================
# One frame of events: API requests and webhook deliveries
# =============================================================================

def events_frame(requests: pd.DataFrame, webhooks=None) -> pd.DataFrame:
    """Everything that could fail, in time order, in one shape.

    `requests` is the frame from analytics.load_requests(). `webhooks` is the
    list of dicts from GET /lab/webhooks (or db.list_webhook_deliveries), or a
    DataFrame with the same columns. Either may be empty.
    """
    parts = []
    if requests is not None and len(requests):
        api = pd.DataFrame({
            "source": "api",
            "id": requests["id"].astype(int),
            "timestamp": pd.to_datetime(requests["timestamp"], utc=True, format="ISO8601"),
            "partner_id": requests["partner_id"].fillna(UNKNOWN_PARTNER).astype(str),
            "request": requests["request"].astype(str),
            "status_code": requests["status_code"].astype(int),
            "reason_key": _reason_keys(requests["status_code"], requests["failure_reason"]),
            "failed": requests["status_code"].astype(int) >= 400,
            "run_label": requests["run_label"],
            "release_version": requests["release_version"],
            "request_id": requests["request_id"].astype(str),
            "product": requests.get("product"),
            "customer_id": requests.get("customer_id"),
            "application_id": requests.get("application_id"),
            "latency_ms": requests["latency_ms"],
        })
        parts.append(api)

    deliveries = pd.DataFrame(webhooks) if webhooks is not None else pd.DataFrame()
    if len(deliveries):
        failed = deliveries["status"].astype(str) == "failed"
        codes = pd.to_numeric(deliveries["last_status_code"]).fillna(0).astype(int)
        hooks = pd.DataFrame({
            "source": "webhook",
            "id": deliveries["id"].astype(int),
            "timestamp": pd.to_datetime(deliveries["timestamp"], utc=True, format="ISO8601"),
            "partner_id": deliveries["partner_id"].astype(str),
            "request": WEBHOOK_REQUEST,
            "status_code": codes,
            "reason_key": _reason_keys(codes, deliveries["failure_reason"], failed),
            "failed": failed,
            "run_label": None,
            "release_version": None,
            "request_id": "webhook-" + deliveries["id"].astype(str),
            "product": None,
            "customer_id": None,
            "application_id": deliveries["application_id"],
            "latency_ms": np.nan,
        })
        parts.append(hooks)

    if not parts:
        return pd.DataFrame(columns=EVENT_COLUMNS)
    events = pd.concat(parts, ignore_index=True)[EVENT_COLUMNS]
    events = events.sort_values(["timestamp", "source", "id"]).reset_index(drop=True)
    events["seq"] = np.arange(len(events))
    return events


def _reason_keys(status_codes, reasons, failed=None) -> pd.Series:
    """A string key per row: the failure reason, 'http_<status>' for an uncatalogued
    failure, and '' for a success."""
    status = pd.Series(status_codes).astype(int).reset_index(drop=True)
    reason = pd.Series(reasons).reset_index(drop=True)
    is_failed = pd.Series(failed).reset_index(drop=True) if failed is not None else status >= 400
    keys = []
    for code, text, bad in zip(status, reason, is_failed):
        if not bad:
            keys.append("")
        elif isinstance(text, str) and text:
            keys.append(text)
        else:
            keys.append(f"http_{code}")
    return pd.Series(keys, dtype="object")


# =============================================================================
# Episodes
# =============================================================================

def build_episodes(events: pd.DataFrame, gap_seconds: float = GAP_SECONDS,
                   clean_requests: int = CLEAN_REQUESTS, min_occurrences: int = MIN_OCCURRENCES,
                   alpha: float = ALPHA) -> pd.DataFrame:
    """One row per (fingerprint, episode), with each episode tested against the
    history before it. See the module docstring for the rules."""
    rows = []
    if events is None or not len(events):
        return pd.DataFrame(columns=EPISODE_COLUMNS)
    failed = events[events["failed"]]
    for (partner, status, reason), occurrences in failed.groupby(
            ["partner_id", "status_code", "reason_key"], sort=True):
        stream = _exposed_stream(events, partner, occurrences)
        hit = (stream["status_code"] == status) & (stream["reason_key"] == reason)
        hits = stream[hit]
        time_gap = hits["timestamp"].diff().dt.total_seconds()
        clean_between = hits["pos"].diff() - 1
        new_episode = (time_gap > gap_seconds) | (clean_between >= clean_requests)
        episode_ids = new_episode.fillna(False).astype(int).cumsum()
        history_n, history_x, previous_end = 0, 0, -1    # exposed traffic before the episode
        for number, group in hits.groupby(episode_ids, sort=True):
            first_pos, last_pos = int(group["pos"].iloc[0]), int(group["pos"].iloc[-1])
            window = stream.iloc[first_pos:last_pos + 1]
            n, x = len(window), len(group)
            products = group["product"].dropna().astype(str).value_counts().to_dict()

            # The requests between the previous episode and this one are clean by
            # construction (an occurrence there would have joined an episode).
            history_n += first_pos - previous_end - 1
            p0 = (history_x + 0.5) / (history_n + 1)               # Jeffreys estimate
            tail_p = float(stats.binom.sf(x - 1, n, p0)) if history_n > 0 else np.nan
            flagged = bool(history_n > 0 and tail_p < alpha and x >= min_occurrences)
            posterior = stats.beta(0.5 + x, 0.5 + n - x)
            low, high = posterior.ppf([0.025, 0.975])
            rows.append({
                "fingerprint": fingerprint_label(partner, status, reason),
                "partner_id": partner,
                "status_code": int(status),
                "failure_reason": reason,
                "episode": int(number) + 1,
                "start": group["timestamp"].iloc[0],
                "end": group["timestamp"].iloc[-1],
                "occurrences": x,
                "exposed_requests": n,
                "share": x / n,
                "endpoints": sorted(group["request"].unique()),
                "run_labels": sorted(group["run_label"].dropna().astype(str).unique()),
                "release_versions": sorted(group["release_version"].dropna().astype(str).unique()),
                "products": products,
                "customers_affected": int(group["customer_id"].dropna().nunique()),
                "sample_request_ids": list(group["request_id"].astype(str).head(5)),
                "first_pos": first_pos,
                "last_pos": last_pos,
                "baseline_requests": history_n,
                "baseline_failures": history_x,
                "baseline_rate": p0,
                "tail_p": tail_p,
                "flagged": flagged,
                "posterior_mean": float(posterior.mean()),
                "posterior_low": float(low),
                "posterior_high": float(high),
            })
            # An episode recognised as an incident is kept out of the baseline;
            # anything else (background rate, too thin to call) becomes history.
            if not flagged:
                history_n += n
                history_x += x
            previous_end = last_pos
    episodes = pd.DataFrame(rows, columns=EPISODE_COLUMNS)
    return episodes.sort_values(["fingerprint", "episode"]).reset_index(drop=True)


def fingerprint_label(partner: str, status: int, reason: str) -> str:
    return f"{partner} · {int(status)} {reason}"


def _exposed_stream(events: pd.DataFrame, partner: str, occurrences: pd.DataFrame) -> pd.DataFrame:
    """The partner's traffic to the endpoints this failure has ever hit, in order."""
    endpoints = set(occurrences["request"])
    stream = events[(events["partner_id"] == partner) & (events["request"].isin(endpoints))]
    stream = stream.sort_values(["timestamp", "seq"]).reset_index(drop=True)
    stream["pos"] = np.arange(len(stream))
    return stream


# =============================================================================
# The registry: one row per fingerprint, with the statistics
# =============================================================================

def build_registry(events: pd.DataFrame, episodes: pd.DataFrame | None = None,
                   gap_seconds: float = GAP_SECONDS, clean_requests: int = CLEAN_REQUESTS,
                   min_occurrences: int = MIN_OCCURRENCES, alpha: float = ALPHA,
                   now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Per fingerprint: how often it comes back, and whether the latest episode is real.

    `now` decides whether the latest episode is still open. It defaults to the
    time of the last recorded event (log time), because a lab is bursty: people
    pause. A production version would use the wall clock.
    """
    if events is None or not len(events):
        return pd.DataFrame(columns=REGISTRY_COLUMNS)
    if episodes is None:
        episodes = build_episodes(events, gap_seconds, clean_requests, min_occurrences, alpha)
    if not len(episodes):
        return pd.DataFrame(columns=REGISTRY_COLUMNS)
    log_now = events["timestamp"].max() if now is None else pd.Timestamp(now)
    if log_now.tzinfo is None:
        log_now = log_now.tz_localize("UTC")

    rows = []
    for fingerprint, group in episodes.groupby("fingerprint", sort=True):
        group = group.sort_values("episode")
        latest = group.iloc[-1]
        partner, status, reason = latest["partner_id"], int(latest["status_code"]), latest["failure_reason"]
        occurrences = events[(events["partner_id"] == partner) & (events["status_code"] == status)
                             & (events["reason_key"] == reason)]
        stream = _exposed_stream(events, partner, occurrences)

        n, x = int(latest["exposed_requests"]), int(latest["occurrences"])
        clean_n, p0 = int(latest["baseline_requests"]), float(latest["baseline_rate"])
        tail_p, flagged = float(latest["tail_p"]), bool(latest["flagged"])
        low, high = float(latest["posterior_low"]), float(latest["posterior_high"])

        starts = pd.to_datetime(group["start"])
        gaps = starts.diff().dt.total_seconds().dropna()
        median_gap = float(gaps.median()) if len(gaps) else np.nan

        trend, difference, trend_p = "first episode", np.nan, np.nan
        if len(group) >= 2:
            earlier = group.iloc[:-1]
            n_prev, x_prev = int(earlier["exposed_requests"].sum()), int(earlier["occurrences"].sum())
            test = compare_proportions(x_prev, n_prev, x, n)
            difference, trend_p = test["difference"], test["p_value"]
            if trend_p < 0.05 and difference > 0:
                trend = "worse"
            elif trend_p < 0.05 and difference < 0:
                trend = "better"
            else:
                trend = "stable"

        clean_after = len(stream) - 1 - int(latest["last_pos"])
        quiet_for = (log_now - pd.Timestamp(latest["end"])).total_seconds()
        is_open = bool(clean_after < clean_requests and quiet_for <= gap_seconds)

        products = {}
        for item in group["products"]:
            for name, count in item.items():
                products[name] = products.get(name, 0) + count
        key = runbook_key(reason, products)
        book = runbooks.RUNBOOKS.get(key) or runbooks.lookup(status, reason)

        rows.append({
            "fingerprint": fingerprint,
            "partner_id": partner,
            "status_code": status,
            "failure_reason": reason,
            "runbook": key,
            "class": book["class"],
            "owner": book["owner"],
            "status": status_label(is_open, flagged),
            "endpoints": sorted({e for item in group["endpoints"] for e in item}),
            "episodes": int(len(group)),
            "occurrences": int(group["occurrences"].sum()),
            "first_seen": group["start"].iloc[0],
            "last_seen": latest["end"],
            "open": is_open,
            "recurring": bool(len(group) >= 2),
            "median_gap_seconds": median_gap,
            "baseline_clean_requests": clean_n,
            "baseline_failures": int(latest["baseline_failures"]),
            "baseline_rate": p0,
            "latest_start": latest["start"],
            "latest_end": latest["end"],
            "latest_occurrences": x,
            "latest_exposed": n,
            "latest_share": x / n,
            "tail_p": tail_p,
            "flagged": flagged,
            "posterior_mean": float(latest["posterior_mean"]),
            "posterior_low": low,
            "posterior_high": high,
            "trend": trend,
            "trend_difference": difference,
            "trend_p": trend_p,
            "latest_run_labels": latest["run_labels"],
            "latest_release_versions": latest["release_versions"],
            "latest_products": latest["products"],
            "latest_customers": int(latest["customers_affected"]),
            "latest_request_ids": latest["sample_request_ids"],
        })
    registry = pd.DataFrame(rows, columns=REGISTRY_COLUMNS)
    order = {"high": 0, "medium": 1, "low": 2}
    registry["_severity"] = [order.get(runbooks.RUNBOOKS.get(k, {}).get("severity", "medium"), 1)
                             for k in registry["runbook"]]
    registry = registry.sort_values(["flagged", "open", "_severity", "episodes", "last_seen"],
                                    ascending=[False, False, True, False, False])
    return registry.drop(columns="_severity").reset_index(drop=True)


def status_label(is_open: bool, flagged: bool) -> str:
    """One word for the dashboard: what is this fingerprint doing right now?

    active      happening now and statistically real
    resolved    was real, has gone quiet
    watch       happening now, but not distinct from the baseline (yet)
    background  quiet and never distinct from the baseline, e.g. an intended rule
    """
    if flagged:
        return "active" if is_open else "resolved"
    return "watch" if is_open else "background"


def runbook_key(reason: str, products: dict | None) -> str:
    """Which runbook applies. Rule R-204 needs a second look: refusing invoice
    finance is intended; refusing anything else is the v1.1.0 regression."""
    if reason == "rule_R204_amount_band":
        if products and set(products) <= {"invoice_finance"}:
            return "rule_R204_intended"
        return "rule_R204_amount_band"
    return reason if reason in runbooks.RUNBOOKS else reason


# =============================================================================
# Serialisation for the API
# =============================================================================

def to_records(frame: pd.DataFrame) -> list[dict]:
    """JSON-safe rows: timestamps as ISO text, NaN as null, numpy types as Python."""
    records = []
    for row in frame.to_dict(orient="records"):
        clean = {}
        for key, value in row.items():
            if isinstance(value, pd.Timestamp):
                clean[key] = value.isoformat(timespec="milliseconds")
            elif isinstance(value, (np.integer,)):
                clean[key] = int(value)
            elif isinstance(value, (np.floating, float)):
                clean[key] = None if np.isnan(value) else float(value)
            elif isinstance(value, (np.bool_,)):
                clean[key] = bool(value)
            else:
                clean[key] = value
        records.append(clean)
    return records
