"""Builds the numbers returned by GET /metrics from the request log."""

import math

from partnerflow import config, db


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile: the value below which pct% of values fall."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return round(ordered[rank - 1], 2)


def rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def build_metrics(lab: dict, run_label: str | None = None) -> dict:
    """Summarise recorded requests, optionally for one traffic run only."""
    where, params = "", []
    if run_label:
        where, params = " WHERE run_label = ?", [run_label]

    with db.connect() as conn:
        requests = conn.execute(
            "SELECT partner_id, status_code, latency_ms, failure_reason "
            f"FROM request_log{where}", params
        ).fetchall()
        applications = conn.execute(
            "SELECT status, COUNT(*) AS n FROM applications GROUP BY status"
        ).fetchall()

    total = len(requests)
    client_errors = sum(1 for r in requests if 400 <= r["status_code"] < 500)
    server_errors = sum(1 for r in requests if r["status_code"] >= 500)
    latencies = [r["latency_ms"] for r in requests]

    by_status_code: dict[str, int] = {}
    by_failure_reason: dict[str, int] = {}
    for r in requests:
        code = str(r["status_code"])
        by_status_code[code] = by_status_code.get(code, 0) + 1
        if r["failure_reason"]:
            reason = r["failure_reason"]
            by_failure_reason[reason] = by_failure_reason.get(reason, 0) + 1

    by_partner = []
    for partner_id in config.PARTNER_IDS:
        rows = [r for r in requests if r["partner_id"] == partner_id]
        errors = sum(1 for r in rows if r["status_code"] >= 400)
        by_partner.append({
            "partner_id": partner_id,
            "total": len(rows),
            "errors": errors,
            "error_rate": rate(errors, len(rows)),
            "latency_ms_p95": percentile([r["latency_ms"] for r in rows], 95),
        })

    status_counts = {row["status"]: row["n"] for row in applications}
    application_total = sum(status_counts.values())
    version = config.release_version(lab["release"], lab["hotfix"])

    return {
        "run_label": run_label,
        "release": {
            "release": lab["release"],
            "hotfix": lab["hotfix"],
            "version": version,
            "notes": config.RELEASE_NOTES[version],
        },
        "active_faults": [name for name, f in lab["faults"].items() if f["enabled"]],
        "requests": {
            "total": total,
            "succeeded": total - client_errors - server_errors,
            "client_errors": client_errors,
            "server_errors": server_errors,
            "error_rate": rate(client_errors + server_errors, total),
            "latency_ms_p50": percentile(latencies, 50),
            "latency_ms_p95": percentile(latencies, 95),
        },
        "by_partner": by_partner,
        "by_status_code": dict(sorted(by_status_code.items())),
        "by_failure_reason": dict(sorted(by_failure_reason.items(), key=lambda kv: -kv[1])),
        "applications": {
            "total": application_total,
            "approved": status_counts.get("approved", 0),
            "referred": status_counts.get("referred", 0),
            "declined": status_counts.get("declined", 0),
            "approval_rate": rate(status_counts.get("approved", 0), application_total),
        },
    }
