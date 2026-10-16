"""Statistical monitoring, checked against hand calculations and the simulator's truth."""

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from partnerflow import brokersim, monitoring
from partnerflow.brokersim import BUILT_IN_INCIDENT, ERROR_TYPES, Incident, Scenario

BASELINE_HOURS = 7 * 24


def toy_hourly(rows: list[tuple[int, int]]) -> pd.DataFrame:
    """A one-broker hourly frame from (requests, failures) pairs."""
    frame = pd.DataFrame(rows, columns=["requests", "failures"])
    frame["broker"] = "toy"
    frame["hour_index"] = range(len(frame))
    frame["timestamp"] = brokersim.START + pd.to_timedelta(frame["hour_index"], unit="h")
    frame["observed_rate"] = frame["failures"] / frame["requests"].replace(0, np.nan)
    for error_type in ERROR_TYPES:
        frame[f"failures_{error_type}"] = 0
    frame["failures_timeout"] = frame["failures"]
    frame["latency_p50_ms"] = 100.0
    frame["latency_p95_ms"] = 200.0
    frame["incident"] = ""
    return frame


@pytest.fixture(scope="module")
def default():
    out = brokersim.simulate(Scenario())
    out["chart"] = monitoring.control_chart(out["hourly"], BASELINE_HOURS)
    return out


# --- The control chart, by hand -----------------------------------------------

def test_control_chart_matches_hand_calculation():
    """Baseline: 10 hours of 8/400 (2%). Test hour: 20/400.

    sd   = sqrt(0.02 * 0.98 / 400) = 0.007
    ucl  = 0.02 + 3 * 0.007        = 0.041
    z    = (0.05 - 0.02) / 0.007   = 4.2857
    p    = P(X >= 20 | n=400, p=0.02) = binom.sf(19, 400, 0.02)
    """
    chart = monitoring.control_chart(toy_hourly([(400, 8)] * 10 + [(400, 20)]), baseline_hours=10)
    assert chart["expected_rate"].iloc[0] == pytest.approx(0.02)
    test_hour = chart.iloc[-1]
    assert test_hour["sd"] == pytest.approx(0.007, abs=1e-9)
    assert test_hour["ucl"] == pytest.approx(0.041, abs=1e-9)
    assert test_hour["lcl"] == pytest.approx(0.0, abs=1e-9)          # 0.02 - 0.021 clipped at 0
    assert test_hour["z"] == pytest.approx(4.2857, abs=1e-4)
    assert test_hour["p_value"] == pytest.approx(stats.binom.sf(19, 400, 0.02))
    # The exact tail (2.1e-4) is fatter than the normal tail at 4.3 sigma (1e-5), and
    # still far below the 1.35e-3 threshold, so the hour is flagged.
    assert 1e-4 < test_hour["p_value"] < 3e-4
    assert bool(test_hour["flagged"])
    assert not chart["flagged"].iloc[:10].any()
    low, high = chart[["ci_low", "ci_high"]].iloc[-1]
    assert low < 0.05 < high


def test_exact_tail_is_stricter_than_the_normal_rule_for_small_counts():
    """30 requests, 3 failures, expected 2%: z says 3.1 sigma, the exact tail says 2%."""
    chart = monitoring.control_chart(toy_hourly([(400, 8)] * 10 + [(30, 3)]), baseline_hours=10)
    test_hour = chart.iloc[-1]
    assert test_hour["z"] > 3
    assert test_hour["p_value"] == pytest.approx(stats.binom.sf(2, 30, 0.02))
    assert test_hour["p_value"] > 0.02
    assert not bool(test_hour["flagged"])


def test_an_hour_with_no_requests_is_handled():
    chart = monitoring.control_chart(toy_hourly([(400, 8)] * 10 + [(0, 0)]), baseline_hours=10)
    test_hour = chart.iloc[-1]
    assert np.isnan(test_hour["sd"]) and np.isnan(test_hour["z"])
    assert test_hour["p_value"] == 1.0
    assert not bool(test_hour["flagged"])


def test_expected_false_alarms():
    assert monitoring.ALPHA_3_SIGMA == pytest.approx(0.00135, abs=1e-5)
    assert monitoring.expected_false_alarms(1680) == pytest.approx(2.27, abs=0.01)


# --- On the simulated estate ----------------------------------------------------

def test_the_built_in_incident_is_detected_with_few_false_alarms(default):
    chart = default["chart"]
    flagged = chart[chart["flagged"]]
    inside = flagged[flagged["incident"] != ""]
    outside = flagged[flagged["incident"] == ""]
    assert len(inside) == 8                                   # every incident hour
    assert len(outside) <= 4                                  # 2.3 expected by chance
    # The normal 3-sigma rule would have raised more false alarms.
    normal_rule = chart[(chart["z"] > 3) & (chart["incident"] == "")]
    assert len(normal_rule) >= len(outside)


def test_incidents_are_grouped_with_the_right_evidence(default):
    incidents = monitoring.group_incidents(default["chart"], BASELINE_HOURS)
    top = incidents.iloc[0]
    assert top["broker"] == "meridian"
    assert (top["start_hour"], top["end_hour"], top["hours"]) == (225, 232, 8)
    assert top["observed_rate"] > 0.12 and top["expected_rate"] == pytest.approx(0.04, abs=0.005)
    assert top["excess_failures"] > 800
    assert top["dominant_error_type"] == "timeout"
    assert top["dominant_share"] > top["dominant_share_baseline"] + 0.2
    assert top["latency_p95_ms"] > 2 * top["latency_p95_baseline_ms"]
    assert top["min_p_value"] < 1e-20
    assert not top["in_baseline"]
    assert (incidents["hours"].iloc[1:] <= 2).all()           # anything else is a blip


def test_a_one_hour_user_fault_is_still_detected():
    fault = Incident("quill", start_hour=300, duration_hours=1, failure_rate=0.30,
                     error_type="authentication", label="my fault")
    out = brokersim.simulate(Scenario(incidents=(BUILT_IN_INCIDENT, fault)))
    chart = monitoring.control_chart(out["hourly"], BASELINE_HOURS)
    incidents = monitoring.group_incidents(chart, BASELINE_HOURS)
    quill = incidents[incidents["broker"] == "quill"]
    assert len(quill) >= 1
    assert quill.iloc[0]["start_hour"] == 300 and quill.iloc[0]["hours"] == 1
    assert quill.iloc[0]["dominant_error_type"] == "authentication"


def test_broker_summary_variance_and_dispersion(default):
    summary = monitoring.broker_summary(default["chart"], BASELINE_HOURS).set_index("broker")
    for broker in ("northgate", "harbourline", "ashgrove", "quill"):
        row = summary.loc[broker]
        assert row["observed_rate"] == pytest.approx(row["design_rate"], abs=0.003)
        assert row["variance"] == pytest.approx(row["observed_rate"] * (1 - row["observed_rate"]) / row["requests"])
        assert row["sd"] == pytest.approx(np.sqrt(row["variance"]))
        assert 0.7 < row["dispersion"] < 1.4                  # wobble matches binomial noise
        assert row["flagged_hours"] <= 2
    meridian = summary.loc["meridian"]
    assert meridian["dispersion"] > 1.8                       # the incident inflates the variance
    assert meridian["flagged_hours"] >= 8
    # More traffic, narrower interval.
    assert (summary.loc["northgate", "ci_high"] - summary.loc["northgate", "ci_low"]) < \
           (summary.loc["quill", "ci_high"] - summary.loc["quill", "ci_low"])


def test_completion_evidence_brackets_the_true_effect(default):
    evidence = monitoring.completion_evidence(default["applications"], default["hourly"],
                                              "meridian", 225, 232, BASELINE_HOURS)
    assert evidence["exposure_during"] > 2 * evidence["exposure_before"]
    assert evidence["did"] < 0 and evidence["naive_change"] < 0
    low, high = evidence["did_ci"]
    assert low <= evidence["truth"] <= high
    assert high < 0                                           # clearly not chance
    assert evidence["cross_broker_r"] < -0.8                  # strong, confounded correlation
    assert len(evidence["cross_broker"]) == 5
    assert evidence["lost_applications"] > 20
    assert evidence["lost_loan_value"] > 0


def test_applications_affected_lists_the_right_customers(default):
    affected = monitoring.applications_affected(default["applications"], "meridian", 225, 232)
    assert len(affected) > 100
    assert affected["had_api_error"].all()
    assert affected["customer_id"].is_unique
    assert affected["timestamp"].between(brokersim.START + pd.Timedelta(hours=225),
                                         brokersim.START + pd.Timedelta(hours=233)).all()


def test_classifier_report(default):
    report = monitoring.classifier_report(default["tickets"])
    matrix = report["matrix"]
    assert matrix.shape == (5, 5)
    assert matrix.to_numpy().sum() == len(default["tickets"])
    assert report["accuracy"] == pytest.approx(np.trace(matrix.to_numpy()) / len(default["tickets"]))
    assert 0.8 <= report["accuracy"] < 1.0
    per_category = report["per_category"].set_index("category")
    assert per_category.loc["authentication", "recall"] > 0.9
    assert per_category.loc["other", "precision"] < per_category.loc["validation", "precision"]
