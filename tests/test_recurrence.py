"""Recurring issues: episodes, the sequential baseline and the registry, by hand."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from partnerflow import recurrence, runbooks

T0 = pd.Timestamp("2026-10-20T10:00:00+00:00")


def requests_frame(rows: list[tuple]) -> pd.DataFrame:
    """Rows of (partner, request, status, reason, seconds) -> a load_requests()-like frame.

    An optional sixth item is the product (for the R-204 check).
    """
    records = []
    for index, row in enumerate(rows):
        partner, request, status, reason, seconds = row[:5]
        method, endpoint = request.split(" ", 1)
        records.append({
            "id": index + 1,
            "timestamp": (T0 + pd.Timedelta(seconds=seconds)).isoformat(timespec="milliseconds"),
            "request_id": f"req{index + 1:04d}",
            "partner_id": partner,
            "method": method, "endpoint": endpoint, "path": endpoint,
            "request": request,
            "status_code": status,
            "latency_ms": 10.0,
            "failure_reason": reason,
            "release_version": "v1.0.0",
            "run_label": "test",
            "product": row[5] if len(row) > 5 else np.nan,
            "customer_id": np.nan, "application_id": np.nan,
        })
    return pd.DataFrame(records)


def burst(partner: str, request: str, status: int, reason: str, count: int, start: float) -> list[tuple]:
    return [(partner, request, status, reason, start + i) for i in range(count)]


def clean(partner: str, request: str, count: int, start: float) -> list[tuple]:
    return [(partner, request, 201, None, start + i) for i in range(count)]


# --- Episodes ------------------------------------------------------------------

def test_clean_requests_between_occurrences_split_an_episode():
    rows = (clean("cedar", "POST /customers", 20, 0)
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 5, 100)
            + clean("cedar", "POST /customers", 10, 200)          # exactly the threshold
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 5, 300))
    events = recurrence.events_frame(requests_frame(rows))
    episodes = recurrence.build_episodes(events, gap_seconds=10_000, clean_requests=10)
    assert list(episodes["episode"]) == [1, 2]
    assert list(episodes["occurrences"]) == [5, 5]
    assert list(episodes["exposed_requests"]) == [5, 5]

    # Nine clean requests are not enough: one episode spanning the lot.
    rows = (clean("cedar", "POST /customers", 20, 0)
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 5, 100)
            + clean("cedar", "POST /customers", 9, 200)
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 5, 300))
    events = recurrence.events_frame(requests_frame(rows))
    episodes = recurrence.build_episodes(events, gap_seconds=10_000, clean_requests=10)
    assert list(episodes["episode"]) == [1]
    assert int(episodes["exposed_requests"].iloc[0]) == 19


def test_a_quiet_gap_in_time_splits_an_episode_even_with_no_traffic_between():
    rows = (clean("cedar", "POST /customers", 20, 0)
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 4, 100)
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 4, 100 + 4 + 500))
    events = recurrence.events_frame(requests_frame(rows))
    assert len(recurrence.build_episodes(events, gap_seconds=120, clean_requests=10)) == 2
    assert len(recurrence.build_episodes(events, gap_seconds=1000, clean_requests=10)) == 1


def test_exposure_is_the_partners_traffic_to_the_endpoints_the_failure_hit():
    """Other partners and other endpoints never dilute or split an episode."""
    rows = (clean("cedar", "POST /customers", 10, 0)
            + clean("alder", "POST /customers", 50, 10)                 # another partner, in between
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 3, 100)
            + clean("cedar", "GET /applications/{application_id}", 50, 110)   # another endpoint
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 3, 200))
    events = recurrence.events_frame(requests_frame(rows))
    episodes = recurrence.build_episodes(events, gap_seconds=10_000, clean_requests=10)
    assert len(episodes) == 1, "alder's traffic and cedar's GETs are not exposed traffic"
    assert int(episodes["exposed_requests"].iloc[0]) == 6


# --- The sequential baseline ------------------------------------------------------

def test_first_episode_is_tested_against_the_clean_history_by_hand():
    rows = clean("cedar", "POST /customers", 40, 0) + burst("cedar", "POST /customers", 401, "api_key_revoked", 6, 100)
    events = recurrence.events_frame(requests_frame(rows))
    episode = recurrence.build_episodes(events).iloc[0]
    p0 = 0.5 / 41                                   # Jeffreys: no failures in 40 requests
    assert episode["baseline_requests"] == 40 and episode["baseline_failures"] == 0
    assert episode["baseline_rate"] == pytest.approx(p0)
    assert episode["tail_p"] == pytest.approx(stats.binom.sf(5, 6, p0))   # P(X >= 6 | 6, p0) = p0 ** 6
    assert bool(episode["flagged"])
    posterior = stats.beta(6.5, 0.5)
    assert episode["posterior_mean"] == pytest.approx(posterior.mean())


def test_three_occurrences_are_a_pattern_two_are_an_anecdote():
    rows = clean("cedar", "POST /customers", 200, 0) + burst("cedar", "POST /customers", 401, "api_key_revoked", 2, 300)
    events = recurrence.events_frame(requests_frame(rows))
    two = recurrence.build_episodes(events).iloc[0]
    assert two["tail_p"] < recurrence.ALPHA and not two["flagged"], "too few to call"

    rows = clean("cedar", "POST /customers", 200, 0) + burst("cedar", "POST /customers", 401, "api_key_revoked", 3, 300)
    three = recurrence.build_episodes(recurrence.events_frame(requests_frame(rows))).iloc[0]
    assert bool(three["flagged"])


def test_no_history_means_no_test():
    rows = burst("cedar", "POST /customers", 401, "api_key_revoked", 10, 0)
    episode = recurrence.build_episodes(recurrence.events_frame(requests_frame(rows))).iloc[0]
    assert episode["baseline_requests"] == 0
    assert np.isnan(episode["tail_p"]) and not episode["flagged"]
    registry = recurrence.build_registry(recurrence.events_frame(requests_frame(rows)))
    assert registry["status"].iloc[0] == "watch"


def test_a_recognised_incident_is_kept_out_of_the_baseline_so_its_recurrence_is_flagged():
    rows = (clean("cedar", "POST /customers", 40, 0)
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 8, 100)
            + clean("cedar", "POST /customers", 40, 200)
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 8, 300))
    episodes = recurrence.build_episodes(recurrence.events_frame(requests_frame(rows)))
    first, second = episodes.iloc[0], episodes.iloc[1]
    assert bool(first["flagged"])
    # The second episode's baseline is the 80 clean requests only: the first
    # episode was an incident, not part of "normal".
    assert second["baseline_requests"] == 80 and second["baseline_failures"] == 0
    assert bool(second["flagged"])
    registry = recurrence.build_registry(recurrence.events_frame(requests_frame(rows)))
    row = registry.iloc[0]
    assert row["episodes"] == 2 and bool(row["recurring"]) and row["status"] == "active"
    assert row["median_gap_seconds"] == pytest.approx(200.0)


def test_a_steady_background_rate_is_never_an_incident():
    """An intended rule refusing one application in ten, spread evenly, forms many
    small 'episodes' but each is in line with the history before it."""
    rows = []
    for i in range(400):
        if i % 10 == 9:
            rows.append(("alder", "POST /applications", 422, "rule_R101_product_limits", i))
        else:
            rows.append(("alder", "POST /applications", 201, None, i))
    episodes = recurrence.build_episodes(recurrence.events_frame(requests_frame(rows)), clean_requests=5)
    assert len(episodes) >= 10, "sporadic refusals, each its own episode"
    assert not episodes["flagged"].any()
    registry = recurrence.build_registry(recurrence.events_frame(requests_frame(rows)), clean_requests=5)
    row = registry.iloc[0]
    assert row["status"] in ("watch", "background") and row["runbook"] == "rule_R101_product_limits"
    assert row["owner"] == "partner"
    # The baseline converges on the true background rate.
    assert row["baseline_rate"] == pytest.approx(0.1, abs=0.02)


def test_the_same_rate_concentrated_in_a_burst_is_an_incident():
    rows = clean("alder", "POST /applications", 360, 0) + burst("alder", "POST /applications", 500, "server_exception", 40, 400)
    row = recurrence.build_registry(recurrence.events_frame(requests_frame(rows))).iloc[0]
    assert row["status"] == "active" and row["owner"] == "us" and row["class"] == "server"


# --- Trend and open/closed ----------------------------------------------------------

def test_trend_compares_the_latest_episode_share_with_the_earlier_ones():
    # Episode 1: one refusal in every five requests (20%). Episode 2: every request (100%).
    sparse = []
    for i in range(20):
        reason = "validation_error" if i % 5 == 0 else None
        sparse.append(("birch", "POST /applications", 422 if reason else 201, reason, 100 + i))
    rows = (clean("birch", "POST /applications", 60, 0) + sparse
            + clean("birch", "POST /applications", 60, 200)
            + burst("birch", "POST /applications", 422, "validation_error", 20, 300))
    row = recurrence.build_registry(recurrence.events_frame(requests_frame(rows))).iloc[0]
    assert row["episodes"] == 2 and row["trend"] == "worse" and row["trend_p"] < 0.05
    assert row["trend_difference"] == pytest.approx(1.0 - 4 / 16)


def test_an_episode_followed_by_a_quiet_gap_is_closed():
    rows = (clean("cedar", "POST /customers", 40, 0)
            + burst("cedar", "POST /customers", 401, "api_key_revoked", 6, 100)
            + clean("cedar", "POST /customers", 30, 200))
    registry = recurrence.build_registry(recurrence.events_frame(requests_frame(rows)))
    assert not registry["open"].iloc[0] and registry["status"].iloc[0] == "resolved"
    # The same story, with the wall clock long after the last event: still closed.
    registry = recurrence.build_registry(recurrence.events_frame(requests_frame(rows)),
                                         now=T0 + pd.Timedelta(days=1))
    assert not registry["open"].iloc[0]


# --- Webhooks and runbooks -----------------------------------------------------------

def test_failed_webhook_deliveries_are_a_fingerprint_of_their_own():
    deliveries = []
    for i in range(30):
        deliveries.append({"id": i + 1, "timestamp": (T0 + pd.Timedelta(seconds=i)).isoformat(),
                           "application_id": i + 1, "partner_id": "cedar", "event": "application.decided",
                           "attempts": 1, "status": "delivered", "last_status_code": 200, "failure_reason": None})
    for i in range(30, 36):
        deliveries.append({"id": i + 1, "timestamp": (T0 + pd.Timedelta(seconds=i)).isoformat(),
                           "application_id": i + 1, "partner_id": "cedar", "event": "application.decided",
                           "attempts": 3, "status": "failed", "last_status_code": 503,
                           "failure_reason": "receiver_unavailable"})
    events = recurrence.events_frame(requests_frame(clean("cedar", "POST /customers", 5, 0)), deliveries)
    registry = recurrence.build_registry(events)
    row = registry.iloc[0]
    assert row["fingerprint"] == "cedar · 503 receiver_unavailable"
    assert row["endpoints"] == [recurrence.WEBHOOK_REQUEST]
    assert row["baseline_clean_requests"] == 30 and row["latest_occurrences"] == 6
    assert bool(row["flagged"]) and row["runbook"] == "receiver_unavailable"
    assert runbooks.RUNBOOKS[row["runbook"]]["auto_action"] == "redeliver_webhooks"


def test_rule_r204_is_a_regression_unless_only_invoice_finance_is_refused():
    assert recurrence.runbook_key("rule_R204_amount_band", {"invoice_finance": 4}) == "rule_R204_intended"
    assert recurrence.runbook_key("rule_R204_amount_band", {"term_loan": 3, "invoice_finance": 1}) == "rule_R204_amount_band"
    assert recurrence.runbook_key("rule_R204_amount_band", {}) == "rule_R204_amount_band"
    rows = (clean("alder", "POST /applications", 60, 0)
            + [("alder", "POST /applications", 422, "rule_R204_amount_band", 100 + i, "term_loan") for i in range(5)])
    row = recurrence.build_registry(recurrence.events_frame(requests_frame(rows))).iloc[0]
    assert row["runbook"] == "rule_R204_amount_band" and row["owner"] == "us" and row["class"] == "release-regression"


def test_every_runbook_message_fills_with_the_standard_fields():
    fields = {"partner": "P", "count": 1, "n": 2, "share": "50%", "endpoints": "e", "window": "w",
              "request_id": "r", "products": "p", "episodes": 1}
    for name, book in runbooks.RUNBOOKS.items():
        assert book["owner"] in ("us", "partner", "both"), name
        assert book["auto_action"] in runbooks.AUTO_ACTIONS or book["auto_action"] is None, name
        assert book["partner_message"].format(**fields)


def test_records_are_json_safe():
    import json
    rows = clean("cedar", "POST /customers", 20, 0) + burst("cedar", "POST /customers", 401, "api_key_revoked", 4, 100)
    events = recurrence.events_frame(requests_frame(rows))
    registry = recurrence.build_registry(events)
    text = json.dumps(recurrence.to_records(registry))
    assert "cedar" in text and "NaN" not in text


def test_empty_log_gives_empty_frames():
    events = recurrence.events_frame(pd.DataFrame(), [])
    assert events.empty
    assert recurrence.build_episodes(events).empty
    assert recurrence.build_registry(events).empty
