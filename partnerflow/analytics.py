"""Analytics for PartnerFlow Lab.

Every function takes a pandas DataFrame and returns computed results. Nothing
is hardcoded: change the data and the numbers change.

Two kinds of data are analysed:

* the REQUEST LOG  - genuine events recorded by the API (load_requests)
* JOURNEYS         - the synthetic dataset from partnerflow/synthetic.py

Statistics used, all for proportions (a count of "yes" out of n):

* Wilson interval           - uncertainty around one proportion
* Wald interval + z-test    - uncertainty around the difference of two
* Chi-square test           - do failure rates differ between segments
"""

import json
import sqlite3
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pandas as pd
from scipy import stats

from partnerflow import config
from partnerflow.synthetic import STAGES

AMOUNT_BAND_EDGES = [0, 10_000, 25_000, 50_001, 100_001, float("inf")]
AMOUNT_BAND_LABELS = ["under 10k", "10k-25k", "25k-50k", "50k-100k", "over 100k"]


# =============================================================================
# Loading the request log
# =============================================================================

def load_requests(db_file: Path | None = None) -> pd.DataFrame:
    """Read the whole request log into a DataFrame, opened read-only.

    The JSON in the `metadata` column is expanded into ordinary columns
    (product, amount, journey, rule_id, ...), and two helper columns are added:
    `failed` (status 400 or above) and `amount_band`.
    """
    db_file = Path(db_file or config.db_path())
    columns = ["id", "timestamp", "request_id", "partner_id", "method", "endpoint", "path",
               "status_code", "latency_ms", "failure_reason", "release_version", "run_label"]
    if not db_file.exists():
        frame = pd.DataFrame(columns=columns + ["metadata"])
    else:
        # mode=ro: this code can read but never change the database.
        uri = f"file:{quote(str(db_file))}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            frame = pd.read_sql_query("SELECT * FROM request_log ORDER BY id", conn)
        finally:
            conn.close()

    extra = pd.json_normalize([json.loads(text) for text in frame["metadata"]])
    extra.index = frame.index
    frame = pd.concat([frame.drop(columns="metadata"), extra], axis=1)
    for column in ("product", "amount", "journey", "rule_id", "application_id", "customer_id"):
        if column not in frame:
            frame[column] = np.nan

    frame["failed"] = frame["status_code"] >= 400
    frame["request"] = frame["method"] + " " + frame["endpoint"]
    frame["amount_band"] = pd.cut(pd.to_numeric(frame["amount"]), bins=AMOUNT_BAND_EDGES,
                                  labels=AMOUNT_BAND_LABELS, right=False).astype("object")
    return frame


# =============================================================================
# Statistics helpers
# =============================================================================

def wilson_interval(successes: int, n: int, confidence: float = 0.95) -> tuple[float, float]:
    """Confidence interval for one proportion.

    Preferred over the simple "p plus or minus" formula because it stays
    inside 0..1 and behaves well for small samples.
    """
    if n == 0:
        return (float("nan"), float("nan"))
    z = stats.norm.ppf(1 - (1 - confidence) / 2)
    p = successes / n
    centre = (p + z**2 / (2 * n)) / (1 + z**2 / n)
    half_width = z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / (1 + z**2 / n)
    return (float(centre - half_width), float(centre + half_width))


def compare_proportions(successes_a: int, n_a: int, successes_b: int, n_b: int,
                        confidence: float = 0.95) -> dict:
    """Compare two proportions: B minus A, with a confidence interval and p-value.

    * The interval uses each group's own variance (Wald interval).
    * The p-value tests "there is no difference" using the pooled proportion
      (the standard two-proportion z-test).
    """
    if n_a == 0 or n_b == 0:
        raise ValueError("Both groups need at least one observation.")
    p_a, p_b = successes_a / n_a, successes_b / n_b
    difference = p_b - p_a

    z_critical = stats.norm.ppf(1 - (1 - confidence) / 2)
    standard_error = np.sqrt(p_a * (1 - p_a) / n_a + p_b * (1 - p_b) / n_b)

    pooled = (successes_a + successes_b) / (n_a + n_b)
    pooled_error = np.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    z = difference / pooled_error if pooled_error > 0 else 0.0
    p_value = 2 * stats.norm.sf(abs(z))

    return {
        "n_a": int(n_a), "successes_a": int(successes_a), "rate_a": float(p_a),
        "n_b": int(n_b), "successes_b": int(successes_b), "rate_b": float(p_b),
        "difference": float(difference),
        "standard_error": float(standard_error),
        "ci_low": float(difference - z_critical * standard_error),
        "ci_high": float(difference + z_critical * standard_error),
        "confidence": confidence,
        "z": float(z),
        "p_value": float(p_value),
    }


def _rate_table(frame: pd.DataFrame, by: str | list[str], flag: str) -> pd.DataFrame:
    """Count rows and `flag` rows per group, with a rate and Wilson interval."""
    grouped = frame.groupby(by, dropna=False)[flag].agg(n="size", count="sum").reset_index()
    grouped["count"] = grouped["count"].astype(int)
    grouped["rate"] = grouped["count"] / grouped["n"]
    intervals = [wilson_interval(c, n) for c, n in zip(grouped["count"], grouped["n"])]
    grouped["ci_low"] = [low for low, _ in intervals]
    grouped["ci_high"] = [high for _, high in intervals]
    return grouped


# =============================================================================
# Request log: error rates, release changes, disproportionate failures
# =============================================================================

def error_rates(requests: pd.DataFrame, by: str | list[str]) -> pd.DataFrame:
    """API error rate per group, e.g. by partner_id, endpoint or release_version."""
    table = _rate_table(requests, by, "failed")
    return table.rename(columns={"n": "requests", "count": "failures", "rate": "failure_rate"})


def compare_failure_rates(requests: pd.DataFrame, column: str, before, after) -> dict:
    """Did the failure rate change between two values of `column`?

    Example: compare_failure_rates(log, "run_label", "before-release", "after-release")
    """
    a = requests[requests[column] == before]
    b = requests[requests[column] == after]
    result = compare_proportions(int(a["failed"].sum()), len(a), int(b["failed"].sum()), len(b))
    result.update(column=column, before=before, after=after)
    return result


def detect_release_changes(requests: pd.DataFrame, alpha: float = 0.01) -> pd.DataFrame:
    """Compare each release with the one before it, per endpoint.

    Releases are ordered by when they first appear in the log. A change is
    flagged when the p-value is below `alpha`. With several endpoints tested
    at once, a strict alpha keeps false alarms down.
    """
    if requests.empty:
        return pd.DataFrame()
    order = requests.groupby("release_version")["id"].min().sort_values().index.tolist()
    rows = []
    for previous, current in zip(order, order[1:]):
        for request_name, group in requests.groupby("request"):
            a = group[group["release_version"] == previous]
            b = group[group["release_version"] == current]
            if len(a) == 0 or len(b) == 0:
                continue
            result = compare_proportions(int(a["failed"].sum()), len(a),
                                         int(b["failed"].sum()), len(b))
            rows.append({
                "request": request_name, "from_release": previous, "to_release": current,
                "requests_before": result["n_a"], "failure_rate_before": result["rate_a"],
                "requests_after": result["n_b"], "failure_rate_after": result["rate_b"],
                "change": result["difference"], "ci_low": result["ci_low"],
                "ci_high": result["ci_high"], "p_value": result["p_value"],
                "flagged": bool(result["p_value"] < alpha),
            })
    return pd.DataFrame(rows)


def failure_concentration(requests: pd.DataFrame, by: str | list[str]) -> tuple[pd.DataFrame, dict]:
    """Which segments fail more than their share of traffic would predict?

    `lift` is the segment's failure rate divided by the overall failure rate:
    1.0 means typical, 3.0 means three times the overall rate.

    Also returns a chi-square test of "the failure rate is the same in every
    segment". A small p-value says the differences are unlikely to be chance.
    """
    table = error_rates(requests, by)
    total_requests, total_failures = table["requests"].sum(), table["failures"].sum()
    overall_rate = total_failures / total_requests if total_requests else float("nan")
    table["share_of_requests"] = table["requests"] / total_requests
    table["share_of_failures"] = table["failures"] / total_failures if total_failures else 0.0
    table["lift"] = table["failure_rate"] / overall_rate if overall_rate else float("nan")
    table = table.sort_values("lift", ascending=False).reset_index(drop=True)

    test = {"overall_failure_rate": float(overall_rate), "chi2": None, "p_value": None, "valid": False}
    counts = np.array([table["failures"], table["requests"] - table["failures"]])
    # The test needs at least two segments and both outcomes to be present.
    if counts.shape[1] >= 2 and counts.sum(axis=1).min() > 0:
        result = stats.chi2_contingency(counts)
        test.update(chi2=float(result.statistic), p_value=float(result.pvalue),
                    valid=bool(result.expected_freq.min() >= 5))   # usual rule of thumb
    return table, test


def paired_flips(requests: pd.DataFrame, before_label: str, after_label: str) -> pd.DataFrame:
    """Find requests that succeeded in one run and failed in the other.

    Works because both runs were generated from the same seed: journey 17,
    step "POST /applications" is the identical request in both runs.
    """
    keys = ["journey", "request"]
    columns = keys + ["partner_id", "product", "amount", "amount_band",
                      "status_code", "failure_reason", "request_id"]
    before = requests.loc[requests["run_label"] == before_label, columns]
    after = requests.loc[requests["run_label"] == after_label, columns]
    merged = before.merge(after, on=keys, suffixes=("_before", "_after"))
    flipped = merged[(merged["status_code_before"] < 400) & (merged["status_code_after"] >= 400)]
    return flipped.rename(columns={
        "partner_id_after": "partner_id", "product_after": "product",
        "amount_after": "amount", "amount_band_after": "amount_band",
    })[["journey", "request", "partner_id", "product", "amount", "amount_band",
        "status_code_before", "status_code_after", "failure_reason_after",
        "request_id_after"]].reset_index(drop=True)


# =============================================================================
# Journeys: funnel, partner comparison
# =============================================================================

def conversion_funnel(journeys: pd.DataFrame, group_by: str | None = None) -> pd.DataFrame:
    """How many journeys reach each stage.

    `share_of_started` is cumulative (out of everyone who started).
    `step_conversion` is out of those who reached the previous stage.
    """
    stage_number = journeys["stage_reached"].map({stage: i for i, stage in enumerate(STAGES)})
    groups = [("all", journeys.index)] if group_by is None else journeys.groupby(group_by).groups.items()
    rows = []
    for name, index in groups:
        reached = stage_number.loc[index]
        previous = None
        for i, stage in enumerate(STAGES):
            count = int((reached >= i).sum())
            rows.append({
                "group": name, "stage": stage, "journeys": count,
                "share_of_started": count / len(reached) if len(reached) else float("nan"),
                "step_conversion": count / previous if previous else float("nan"),
            })
            previous = count
    funnel = pd.DataFrame(rows)
    return funnel.drop(columns="group") if group_by is None else funnel.rename(columns={"group": group_by})


def partner_comparison(journeys: pd.DataFrame) -> pd.DataFrame:
    """Per partner: volume, conversion to funded (with interval), errors and latency."""
    table = _rate_table(journeys, "partner", "funded").rename(columns={
        "n": "journeys", "count": "funded", "rate": "conversion",
        "ci_low": "conversion_ci_low", "ci_high": "conversion_ci_high"})
    extra = journeys.groupby("partner").agg(
        submit_rate=("submitted", "mean"),
        api_error_rate=("had_api_error", "mean"),
        median_amount=("loan_amount", "median"),
        latency_ms_p50=("latency_ms", "median"),
        latency_ms_p95=("latency_ms", lambda s: s.quantile(0.95)),
    ).reset_index()
    return table.merge(extra, on="partner")


# =============================================================================
# Journeys: API errors and drop-off (correlation vs causation)
# =============================================================================

def error_dropoff_analysis(journeys: pd.DataFrame,
                           strata: tuple[str, ...] = ("partner", "amount_band")) -> dict:
    """Do journeys with an API error complete (submit) less often?

    Returns three numbers, all "submit rate with error minus without error":

    naive     - the raw comparison. A correlation: journeys with errors also
                differ in other ways (they are larger, from other partners).
    adjusted  - the same comparison made inside each stratum (like with like),
                then averaged by stratum size. Removes the confounding that
                the strata capture. Still only as good as the strata.
    truth     - what the simulator actually built in. Only available because
                the data is synthetic.
    """
    with_error = journeys[journeys["had_api_error"]]
    without_error = journeys[~journeys["had_api_error"]]
    naive = compare_proportions(int(without_error["submitted"].sum()), len(without_error),
                                int(with_error["submitted"].sum()), len(with_error))

    rows, weighted_sum, variance_sum, weight_used = [], 0.0, 0.0, 0.0
    for keys, stratum in journeys.groupby(list(strata)):
        errors = stratum[stratum["had_api_error"]]
        clean = stratum[~stratum["had_api_error"]]
        weight = len(stratum) / len(journeys)
        row = dict(zip(strata, keys)) | {"journeys": len(stratum), "with_error": len(errors)}
        if len(errors) and len(clean):
            p_error, p_clean = errors["submitted"].mean(), clean["submitted"].mean()
            row["submit_rate_with_error"] = p_error
            row["submit_rate_without_error"] = p_clean
            row["difference"] = p_error - p_clean
            weighted_sum += weight * (p_error - p_clean)
            variance_sum += weight**2 * (p_error * (1 - p_error) / len(errors)
                                         + p_clean * (1 - p_clean) / len(clean))
            weight_used += weight
        rows.append(row)

    adjusted = weighted_sum / weight_used if weight_used else float("nan")
    adjusted_error = np.sqrt(variance_sum) / weight_used if weight_used else float("nan")
    z = stats.norm.ppf(0.975)

    truth = None
    if {"would_submit_with_error", "would_submit_without_error"} <= set(journeys.columns):
        truth = float(journeys["would_submit_with_error"].mean()
                      - journeys["would_submit_without_error"].mean())

    return {
        "naive": naive,
        "adjusted_difference": float(adjusted),
        "adjusted_ci_low": float(adjusted - z * adjusted_error),
        "adjusted_ci_high": float(adjusted + z * adjusted_error),
        "share_of_journeys_covered": float(weight_used),
        "true_effect": truth,
        "strata": pd.DataFrame(rows),
    }


# =============================================================================
# Journeys: the randomised A/B experiment
# =============================================================================

def ab_test(journeys: pd.DataFrame, metric: str = "submitted", confidence: float = 0.95) -> dict:
    """Compare control and treatment on a yes/no metric (default: submitted)."""
    control = journeys[journeys["experiment_group"] == "control"]
    treatment = journeys[journeys["experiment_group"] == "treatment"]
    result = compare_proportions(int(control[metric].sum()), len(control),
                                 int(treatment[metric].sum()), len(treatment), confidence)
    return {
        "metric": metric,
        "control_n": result["n_a"], "control_conversions": result["successes_a"],
        "control_rate": result["rate_a"],
        "treatment_n": result["n_b"], "treatment_conversions": result["successes_b"],
        "treatment_rate": result["rate_b"],
        "absolute_effect": result["difference"],
        "relative_effect": result["difference"] / result["rate_a"] if result["rate_a"] else float("nan"),
        "ci_low": result["ci_low"], "ci_high": result["ci_high"],
        "confidence": confidence, "p_value": result["p_value"],
        "sample_size": result["n_a"] + result["n_b"],
    }


def explain_ab(result: dict) -> str:
    """Plain-English reading of an A/B result, written from the computed numbers."""
    points = lambda value: f"{value * 100:+.1f}"
    level = f"{result['confidence']:.0%}"
    lines = [
        f"Treatment converted {result['treatment_rate']:.1%} of {result['treatment_n']:,} journeys; "
        f"control converted {result['control_rate']:.1%} of {result['control_n']:,}.",
        f"The estimated effect is {points(result['absolute_effect'])} percentage points, "
        f"with a {level} confidence interval from {points(result['ci_low'])} to "
        f"{points(result['ci_high'])} points.",
    ]
    if result["ci_low"] > 0 or result["ci_high"] < 0:
        lines.append("The interval does not include zero, so chance alone is an unlikely "
                     "explanation for the difference.")
    else:
        lines.append("The interval includes zero, so this experiment cannot tell a real "
                     "effect from no effect. That is not proof of no effect: the sample "
                     "may simply be too small.")
    lines.append(
        f"The interval is the honest answer, not the single number: if the experiment were "
        f"repeated many times, about {level} of intervals built this way would contain the "
        f"true effect. A larger sample gives a narrower interval."
    )
    return " ".join(lines)
