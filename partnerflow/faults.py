"""Server-side fault injection.

Each function here changes what the API really does when its fault is switched
on. None of them use randomness, so the same traffic gives the same result.

The small amount of state (counters and rate-limit windows) lives in memory in
the API process. It is cleared whenever a fault setting changes or the lab is
reset.
"""

import threading
import time

from partnerflow import db

_lock = threading.Lock()
_rate_windows: dict[str, tuple[float, int]] = {}   # partner_id -> (window_start, count)
_server_error_counter = 0

WEBHOOK_MAX_ATTEMPTS = 3


class ScoringServiceError(RuntimeError):
    """Raised by the simulated scoring service when the server_error fault fires."""


def reset_runtime_state() -> None:
    global _server_error_counter
    with _lock:
        _rate_windows.clear()
        _server_error_counter = 0


def _targets(fault: dict, partner_id: str) -> bool:
    """Is this fault switched on for this partner?"""
    return fault["enabled"] and fault["partner"] in ("all", partner_id)


def key_is_revoked(lab: dict, partner_id: str) -> bool:
    """Scenario A: treat the partner's key as revoked."""
    return _targets(lab["faults"]["auth_revoked"], partner_id)


def rate_limit_retry_after(lab: dict, partner_id: str) -> int | None:
    """Scenario F: return seconds to wait if the partner is over its limit.

    A fixed window: the first request starts a window, and at most `limit`
    requests are allowed until `window_seconds` have passed.
    """
    fault = lab["faults"]["rate_limit"]
    if not _targets(fault, partner_id):
        return None
    now = time.monotonic()
    with _lock:
        window_start, count = _rate_windows.get(partner_id, (now, 0))
        if now - window_start >= fault["window_seconds"]:
            window_start, count = now, 0   # the old window has expired
        if count >= fault["limit"]:
            _rate_windows[partner_id] = (window_start, count)
            return max(1, round(fault["window_seconds"] - (now - window_start)))
        _rate_windows[partner_id] = (window_start, count + 1)
    return None


def injected_delay_ms(lab: dict, partner_id: str) -> int:
    """Scenario E: sleep for the configured time and report how long."""
    fault = lab["faults"]["latency"]
    if not _targets(fault, partner_id) or fault["delay_ms"] <= 0:
        return 0
    time.sleep(fault["delay_ms"] / 1000)
    return fault["delay_ms"]


def call_scoring_service(lab: dict, partner_id: str) -> None:
    """Scenario D: stand-in for a downstream service that sometimes crashes.

    With the fault on, every Nth call raises a real exception. Nothing catches
    it in the endpoint, so it travels up and becomes an HTTP 500.
    """
    global _server_error_counter
    fault = lab["faults"]["server_error"]
    if not _targets(fault, partner_id):
        return
    with _lock:
        _server_error_counter += 1
        call_number = _server_error_counter
    if call_number % fault["every_nth"] == 0:
        raise ScoringServiceError("simulated scoring service did not respond")


def _simulated_partner_receiver(lab: dict, partner_id: str) -> int:
    """The partner's webhook endpoint, simulated in-process. Returns a status code."""
    if _targets(lab["faults"]["webhook_failure"], partner_id):
        return 503
    return 200


def _attempt_delivery(lab: dict, partner_id: str) -> tuple[int, int]:
    """Try the partner's receiver up to WEBHOOK_MAX_ATTEMPTS times.

    Returns (attempts_made, last_status_code). A real system would wait
    between attempts (backoff); the lab retries immediately to stay fast.
    """
    attempts, status_code = 0, 0
    while attempts < WEBHOOK_MAX_ATTEMPTS:
        attempts += 1
        status_code = _simulated_partner_receiver(lab, partner_id)
        if status_code == 200:
            break
    return attempts, status_code


def deliver_webhook(lab: dict, application: dict) -> str:
    """Scenario H: tell the partner about a decision and record the outcome."""
    attempts, status_code = _attempt_delivery(lab, application["partner_id"])
    delivered = status_code == 200
    db.insert_webhook_delivery(
        application_id=application["id"],
        partner_id=application["partner_id"],
        event="application.decided",
        attempts=attempts,
        status="delivered" if delivered else "failed",
        last_status_code=status_code,
        failure_reason=None if delivered else "receiver_unavailable",
    )
    return "delivered" if delivered else "failed"


def redeliver_failed_webhooks(lab: dict) -> dict:
    """Retry every failed delivery. Used after the receiver is healthy again."""
    retried, recovered = 0, 0
    for delivery in db.list_webhook_deliveries(limit=100000, failed_only=True):
        attempts, status_code = _attempt_delivery(lab, delivery["partner_id"])
        delivered = status_code == 200
        db.update_webhook_delivery(
            delivery_id=delivery["id"],
            attempts=delivery["attempts"] + attempts,
            status="delivered" if delivered else "failed",
            last_status_code=status_code,
            failure_reason=None if delivered else "receiver_unavailable",
        )
        retried += 1
        recovered += int(delivered)
    return {"retried": retried, "recovered": recovered, "still_failed": retried - recovered}
