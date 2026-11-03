"""Verify recorded release traffic before treating it as a deterministic replay."""

import pandas as pd

from partnerflow.traffic import build_journeys

VERSIONS = {"before-release": "v1.0.0", "after-release": "v1.1.0", "after-fix": "v1.1.1"}


def validate_run(log: pd.DataFrame, label: str, count: int, seed: int) -> None:
    """Require a complete, unique run with the expected application identities.

    Database-assigned customer/application IDs differ between runs; journey,
    partner, product, amount and term identify the application being replayed.
    Customer creation must succeed for every journey. Operational/client faults
    invalidate this controlled release story instead of becoming release evidence.
    """
    rows = log.loc[log.run_label == label]
    error = f"Run '{label}' is incomplete or differs from its recorded replay settings; reset the lab."
    if rows.empty or count <= 0 or (rows.request == "POST /customers").sum() != count or set(
            rows.release_version) != {VERSIONS[label]}:
        raise ValueError(error)
    expected = build_journeys(seed, count)
    for request in ("POST /customers", "POST /applications"):
        actual = rows.loc[rows.request == request].sort_values("journey")
        if len(actual) != count or actual.journey.tolist() != list(range(count)):
            raise ValueError(error)
        if actual.partner_id.tolist() != [j["partner_id"] for j in expected]:
            raise ValueError(error)
        if request == "POST /customers":
            if not (actual.status_code == 201).all():
                raise ValueError(error)
        else:
            for field in ("product", "amount", "term_months"):
                if field not in actual or actual[field].tolist() != [j["application"][field] for j in expected]:
                    raise ValueError(error)
            refused = actual.loc[actual.status_code != 201]
            if not (refused.status_code == 422).all() or not refused.failure_reason.isin(
                    ["rule_R101_product_limits", "rule_R204_amount_band"]).all():
                raise ValueError(error)
    accepted = rows.loc[(rows.request == "POST /applications") & (rows.status_code == 201)]
    reads = rows.loc[rows.request == "GET /applications/{application_id}"]
    if (len(rows) != 2 * count + len(accepted) or len(reads) != len(accepted)
            or sorted(reads.journey) != sorted(accepted.journey)
            or not (reads.status_code == 200).all()):
        raise ValueError(error)


def recorded_settings(log: pd.DataFrame, label: str = "before-release") -> tuple[int, int]:
    """Recover settings from persistent log metadata, never browser defaults."""
    rows = log.loc[log.run_label == label]
    values = []
    for field in ("replay_count", "replay_seed"):
        if rows.empty or field not in rows or rows[field].isna().any() or rows[field].nunique() != 1:
            raise ValueError("Replay settings are missing or inconsistent; reset the lab to record a new baseline.")
        value = rows[field].iloc[0]
        if value != int(value):
            raise ValueError("Replay settings must be integers; reset the lab.")
        values.append(int(value))
    count, seed = values
    if count <= 0:
        raise ValueError("Replay count must be positive; reset the lab.")
    validate_run(log, label, count, seed)
    return count, seed
