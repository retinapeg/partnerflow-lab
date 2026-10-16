"""Statistical monitoring of broker failure rates, and what failures cost.

Works on the hourly aggregates from partnerflow/brokersim.py. Every number is
computed from the data; the simulator's truth is only used to check answers.

The core idea is the control chart for a proportion (a "p-chart"):

    expected rate p      from a baseline period
    hourly rate  p_hat   = failures / requests in that hour
    sd of p_hat          = sqrt(p (1 - p) / n)          (binomial variance)
    control limits       = p +/- 3 sd                   (3-sigma rule)

An hour is "unusual" when its failure count is very unlikely under the
expected rate. For small counts the normal approximation behind the 3-sigma
rule over-flags, so the flag itself uses the exact binomial tail probability;
z-scores are reported for intuition.
"""

import numpy as np
import pandas as pd
from scipy import stats

from partnerflow import brokersim
from partnerflow.analytics import wilson_interval

ALPHA_3_SIGMA = float(stats.norm.sf(3))     # one-sided tail beyond 3 sd: 0.00135
Z_CRITICAL_95 = float(stats.norm.ppf(0.975))
TYPE_COLUMNS = [f"failures_{t}" for t in brokersim.ERROR_TYPES]


# =============================================================================
# Expected rates and the control chart
# =============================================================================

def baseline_rates(hourly: pd.DataFrame, baseline_hours: int) -> pd.DataFrame:
    """Each broker's expected failure rate, pooled over the baseline window."""
    base = (hourly[hourly["hour_index"] < baseline_hours]
            .groupby("broker")[["requests", "failures"]].sum().reset_index())
    base["expected_rate"] = base["failures"] / base["requests"]
    # The design rate is the simulator's truth; unknown brokers (tests) get NaN.
    base["design_rate"] = [brokersim.BROKERS.get(b, {}).get("failure_rate", np.nan) for b in base["broker"]]
    return base


def control_chart(hourly: pd.DataFrame, baseline_hours: int,
                  alpha: float = ALPHA_3_SIGMA) -> pd.DataFrame:
    """Add expected rate, binomial sd, 3-sigma limits, z, exact p-value and a flag."""
    base = baseline_rates(hourly, baseline_hours)[["broker", "expected_rate"]]
    chart = hourly.merge(base, on="broker", how="left")
    n = chart["requests"].to_numpy().astype(float)
    x = chart["failures"].to_numpy()
    p = chart["expected_rate"].to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        sd = np.sqrt(p * (1 - p) / n)
        z = (x / n - p) / sd
    chart["sd"] = np.where(n > 0, sd, np.nan)
    chart["ucl"] = np.clip(p + 3 * chart["sd"], 0, 1)
    chart["lcl"] = np.clip(p - 3 * chart["sd"], 0, 1)
    chart["z"] = np.where(n > 0, z, np.nan)
    # P(at least x failures | n requests, rate p): the exact upper tail.
    chart["p_value"] = np.where(n > 0, stats.binom.sf(x - 1, n, p), 1.0)
    chart["flagged"] = (chart["p_value"] < alpha) & (n > 0)
    chart["in_baseline"] = chart["hour_index"] < baseline_hours
    intervals = [wilson_interval(int(f), int(r)) for f, r in zip(chart["failures"], chart["requests"])]
    chart["ci_low"] = [low for low, _ in intervals]
    chart["ci_high"] = [high for _, high in intervals]
    return chart


def expected_false_alarms(n_tests: int, alpha: float = ALPHA_3_SIGMA) -> float:
    """How many flags to expect from chance alone when nothing is wrong."""
    return n_tests * alpha


# =============================================================================
# From flagged hours to incidents
# =============================================================================

def group_incidents(chart: pd.DataFrame, baseline_hours: int) -> pd.DataFrame:
    """Merge runs of consecutive flagged hours per broker into incidents."""
    baseline = chart[chart["in_baseline"]]
    baseline_p95 = baseline.groupby("broker")["latency_p95_ms"].median()
    baseline_mix = baseline.groupby("broker")[TYPE_COLUMNS].sum()
    baseline_mix = baseline_mix.div(baseline_mix.sum(axis=1), axis=0)

    rows = []
    for broker, group in chart[chart["flagged"]].groupby("broker"):
        hours = group.sort_values("hour_index")
        run_id = (hours["hour_index"].diff() != 1).cumsum()
        for _, run in hours.groupby(run_id):
            requests, failures = run["requests"].sum(), run["failures"].sum()
            expected = run["expected_rate"].iloc[0]
            mix = run[TYPE_COLUMNS].sum()
            dominant = mix.idxmax().replace("failures_", "")
            p95 = np.average(run["latency_p95_ms"], weights=run["requests"])
            rows.append({
                "broker": broker,
                "start": run["timestamp"].iloc[0],
                "end": run["timestamp"].iloc[-1] + pd.Timedelta(minutes=59),
                "start_hour": int(run["hour_index"].iloc[0]),
                "end_hour": int(run["hour_index"].iloc[-1]),
                "hours": len(run),
                "requests": int(requests),
                "failures": int(failures),
                "observed_rate": failures / requests,
                "expected_rate": expected,
                "excess_failures": int(round(failures - expected * requests)),
                "peak_z": float(run["z"].max()),
                "min_p_value": float(run["p_value"].min()),
                "dominant_error_type": dominant,
                "dominant_share": float(mix.max() / mix.sum()) if mix.sum() else np.nan,
                "dominant_share_baseline": float(baseline_mix.loc[broker, f"failures_{dominant}"]),
                "latency_p95_ms": float(p95),
                "latency_p95_baseline_ms": float(baseline_p95.loc[broker]),
                "in_baseline": bool(run["in_baseline"].any()),
            })
    columns = ["broker", "start", "end", "start_hour", "end_hour", "hours", "requests", "failures",
               "observed_rate", "expected_rate", "excess_failures", "peak_z", "min_p_value",
               "dominant_error_type", "dominant_share", "dominant_share_baseline",
               "latency_p95_ms", "latency_p95_baseline_ms", "in_baseline"]
    incidents = pd.DataFrame(rows, columns=columns)
    return incidents.sort_values("min_p_value").reset_index(drop=True)


# =============================================================================
# Broker comparison
# =============================================================================

def broker_summary(hourly: pd.DataFrame, baseline_hours: int) -> pd.DataFrame:
    """Per broker: observed vs expected rate, interval, variance, dispersion, latency.

    `dispersion` compares how much the hourly rates actually wobble with how
    much binomial sampling alone says they should. Near 1 means the process
    is stable; well above 1 means something beyond chance is moving the rate.
    """
    base = baseline_rates(hourly, baseline_hours).set_index("broker")
    rows = []
    for broker, group in hourly.groupby("broker"):
        n, x = int(group["requests"].sum()), int(group["failures"].sum())
        p = x / n
        low, high = wilson_interval(x, n)
        valid = group[group["requests"] > 0]
        rates = valid["failures"] / valid["requests"]
        expected_var = np.mean(p * (1 - p) / valid["requests"])
        rows.append({
            "broker": broker,
            "name": brokersim.BROKERS[broker]["name"],
            "requests": n,
            "failures": x,
            "observed_rate": p,
            "expected_rate": base.loc[broker, "expected_rate"],
            "design_rate": base.loc[broker, "design_rate"],
            "ci_low": low,
            "ci_high": high,
            "variance": p * (1 - p) / n,
            "sd": np.sqrt(p * (1 - p) / n),
            "hourly_sd_observed": float(rates.std(ddof=1)),
            "hourly_sd_expected": float(np.sqrt(expected_var)),
            "dispersion": float(rates.var(ddof=1) / expected_var),
            "latency_p50_ms": float(group["latency_p50_ms"].median()),
            "latency_p95_ms": float(group["latency_p95_ms"].median()),
            "flagged_hours": int(group["flagged"].sum()) if "flagged" in group else 0,
        })
    return pd.DataFrame(rows).sort_values("requests", ascending=False).reset_index(drop=True)


# =============================================================================
# Lending outcomes: what the failures cost, and how sure we can be
# =============================================================================

def _rate(frame: pd.DataFrame, column: str = "completed") -> tuple[float, int]:
    return (float(frame[column].mean()) if len(frame) else np.nan), len(frame)


def _variance(rate: float, n: int) -> float:
    return rate * (1 - rate) / n if n else np.nan


def completion_evidence(applications: pd.DataFrame, hourly: pd.DataFrame,
                        broker: str, start_hour: int, end_hour: int,
                        baseline_hours: int) -> dict:
    """Three ways of asking 'did the incident cost applications', weakest to strongest.

    cross_broker  Correlation across the five brokers between failure rate and
                  completion. Confounded: brokers differ in many other ways.
    before_during The affected broker's completion before vs during the
                  incident. Better (same broker), but anything else that
                  changed at the same time is mixed in.
    did           Difference-in-differences: the affected broker's change
                  minus the unaffected brokers' change over the same hours.
                  Nets out anything that affected everyone at that time.
    truth         What the simulator actually did to those customers. Only
                  available because the data is synthetic.
    """
    during = (applications["hour_index"] >= start_hour) & (applications["hour_index"] <= end_hour)
    before = applications["hour_index"] < baseline_hours
    affected = applications["broker"] == broker

    ab, n_ab = _rate(applications[affected & before])
    ad, n_ad = _rate(applications[affected & during])
    ob, n_ob = _rate(applications[~affected & before])
    od, n_od = _rate(applications[~affected & during])

    naive_change = ad - ab
    naive_se = np.sqrt(_variance(ad, n_ad) + _variance(ab, n_ab))
    did = (ad - ab) - (od - ob)
    did_se = np.sqrt(_variance(ad, n_ad) + _variance(ab, n_ab) + _variance(od, n_od) + _variance(ob, n_ob))

    # Realised causal effect: completion minus what it would have been with no
    # errors, during the incident, net of the same quantity before it.
    affected_during = applications[affected & during]
    affected_before = applications[affected & before]
    loss_during = (affected_during["would_complete_without_error"].mean() - affected_during["completed"].mean())
    loss_before = (affected_before["would_complete_without_error"].mean() - affected_before["completed"].mean())
    truth = -(loss_during - loss_before)

    # Cross-broker correlation (five points, so treat with care).
    per_broker = hourly.groupby("broker")[["requests", "failures"]].sum()
    per_broker["failure_rate"] = per_broker["failures"] / per_broker["requests"]
    per_broker["completion_rate"] = applications.groupby("broker")["completed"].mean()
    per_broker["applications"] = applications.groupby("broker").size()
    cross_r = float(np.corrcoef(per_broker["failure_rate"], per_broker["completion_rate"])[0, 1])

    # Within the affected broker, hour by hour.
    hourly_apps = (applications[affected].groupby("hour_index")
                   .agg(applications=("completed", "size"), completion=("completed", "mean")))
    hourly_apps = hourly_apps[hourly_apps["applications"] >= 10].join(
        hourly[hourly["broker"] == broker].set_index("hour_index")["observed_rate"])
    within_r = float(hourly_apps[["observed_rate", "completion"]].corr().iloc[0, 1]) if len(hourly_apps) > 3 else np.nan

    lost = max(0.0, (ab - ad)) * n_ad
    return {
        "broker": broker,
        "affected_before": {"rate": ab, "n": n_ab},
        "affected_during": {"rate": ad, "n": n_ad},
        "others_before": {"rate": ob, "n": n_ob},
        "others_during": {"rate": od, "n": n_od},
        "exposure_before": float(affected_before["had_api_error"].mean()) if n_ab else np.nan,
        "exposure_during": float(affected_during["had_api_error"].mean()) if n_ad else np.nan,
        "naive_change": naive_change,
        "naive_ci": (naive_change - Z_CRITICAL_95 * naive_se, naive_change + Z_CRITICAL_95 * naive_se),
        "did": did,
        "did_ci": (did - Z_CRITICAL_95 * did_se, did + Z_CRITICAL_95 * did_se),
        "truth": float(truth),
        "cross_broker": per_broker.reset_index(),
        "cross_broker_r": cross_r,
        "within_broker_hourly": hourly_apps.reset_index(),
        "within_broker_r": within_r,
        "lost_applications": float(lost),
        "lost_loan_value": float(lost * affected_during["loan_amount"].median()) if n_ad else 0.0,
    }


def applications_affected(applications: pd.DataFrame, broker: str,
                          start_hour: int, end_hour: int) -> pd.DataFrame:
    """The customers of one broker whose applications hit an API error in the window."""
    window = applications[(applications["broker"] == broker)
                          & (applications["hour_index"] >= start_hour)
                          & (applications["hour_index"] <= end_hour)]
    columns = ["application_id", "customer_id", "timestamp", "loan_amount", "api_calls",
               "had_api_error", "stage_reached"]
    return window[window["had_api_error"]][columns].sort_values("timestamp").reset_index(drop=True)


# =============================================================================
# Support tickets
# =============================================================================

def classifier_report(tickets: pd.DataFrame) -> dict:
    """Accuracy and confusion matrix of the keyword classifier against the true causes."""
    categories = brokersim.ERROR_TYPES
    matrix = pd.crosstab(tickets["true_category"], tickets["predicted_category"]).reindex(
        index=categories, columns=categories, fill_value=0)
    correct = int(np.trace(matrix.to_numpy()))
    per_category = pd.DataFrame({
        "tickets": matrix.sum(axis=1),
        "recall": np.diag(matrix) / matrix.sum(axis=1).replace(0, np.nan),
        "precision": np.diag(matrix) / matrix.sum(axis=0).replace(0, np.nan),
    })
    return {"accuracy": correct / len(tickets) if len(tickets) else np.nan,
            "matrix": matrix, "per_category": per_category.reset_index(names="category")}


def tickets_by_day(tickets: pd.DataFrame, column: str = "predicted_category") -> pd.DataFrame:
    frame = tickets.assign(day=tickets["timestamp"].dt.floor("D"))
    return frame.groupby(["day", column]).size().reset_index(name="tickets")
