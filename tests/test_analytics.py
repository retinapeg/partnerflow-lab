"""Analytics calculations, checked against numbers worked out by hand."""

import math

import pandas as pd
import pytest

from partnerflow import analytics, synthetic


# --- Statistics helpers -----------------------------------------------------

def test_wilson_interval_matches_hand_calculation():
    # 10 out of 100, 95%: centre 0.11480, half-width 0.05957
    low, high = analytics.wilson_interval(10, 100)
    assert low == pytest.approx(0.05523, abs=1e-5)
    assert high == pytest.approx(0.17437, abs=1e-5)


def test_wilson_interval_stays_inside_zero_to_one():
    low, high = analytics.wilson_interval(0, 20)
    assert low == pytest.approx(0.0, abs=1e-12)
    assert 0 < high < 0.2
    assert all(math.isnan(v) for v in analytics.wilson_interval(0, 0))


def test_compare_proportions_matches_hand_calculation():
    """10/100 versus 15/100.

    difference      = 0.15 - 0.10                               = 0.05
    standard error  = sqrt(0.10*0.90/100 + 0.15*0.85/100)       = 0.046637
    95% interval    = 0.05 +/- 1.959964 * 0.046637              = -0.041407 .. 0.141407
    pooled rate     = 25/200                                    = 0.125
    z               = 0.05 / sqrt(0.125*0.875*(1/100 + 1/100))  = 1.069045
    p-value         = 2 * (1 - Phi(1.069045))                   = 0.28505
    """
    result = analytics.compare_proportions(10, 100, 15, 100)
    assert result["difference"] == pytest.approx(0.05)
    assert result["standard_error"] == pytest.approx(0.046637, abs=1e-6)
    assert result["ci_low"] == pytest.approx(-0.041407, abs=1e-6)
    assert result["ci_high"] == pytest.approx(0.141407, abs=1e-6)
    assert result["z"] == pytest.approx(1.069045, abs=1e-6)
    assert result["p_value"] == pytest.approx(0.28505, abs=1e-5)


def test_compare_proportions_needs_data_in_both_groups():
    with pytest.raises(ValueError):
        analytics.compare_proportions(0, 0, 5, 10)


# --- A/B experiment ---------------------------------------------------------

def ab_frame(control_yes, control_n, treatment_yes, treatment_n) -> pd.DataFrame:
    return pd.DataFrame({
        "experiment_group": ["control"] * control_n + ["treatment"] * treatment_n,
        "submitted": ([True] * control_yes + [False] * (control_n - control_yes)
                      + [True] * treatment_yes + [False] * (treatment_n - treatment_yes)),
    })


def test_ab_test_statistics_match_hand_calculation():
    result = analytics.ab_test(ab_frame(10, 100, 15, 100))
    assert (result["control_n"], result["treatment_n"], result["sample_size"]) == (100, 100, 200)
    assert result["control_rate"] == pytest.approx(0.10)
    assert result["treatment_rate"] == pytest.approx(0.15)
    assert result["absolute_effect"] == pytest.approx(0.05)
    assert result["relative_effect"] == pytest.approx(0.5)
    assert result["ci_low"] == pytest.approx(-0.041407, abs=1e-6)
    assert result["ci_high"] == pytest.approx(0.141407, abs=1e-6)
    assert result["p_value"] == pytest.approx(0.28505, abs=1e-5)


def test_ab_interval_narrows_with_more_data():
    small = analytics.ab_test(ab_frame(10, 100, 15, 100))
    large = analytics.ab_test(ab_frame(1_000, 10_000, 1_500, 10_000))
    assert small["absolute_effect"] == pytest.approx(large["absolute_effect"])
    small_width = small["ci_high"] - small["ci_low"]
    large_width = large["ci_high"] - large["ci_low"]
    assert large_width == pytest.approx(small_width / 10)      # 100x the data, 1/10 the width
    assert small["ci_low"] < 0 < small["ci_high"]              # inconclusive
    assert large["ci_low"] > 0                                 # conclusive


def test_ab_explanation_uses_the_computed_numbers():
    inconclusive = analytics.explain_ab(analytics.ab_test(ab_frame(10, 100, 15, 100)))
    assert "+5.0 percentage points" in inconclusive
    assert "-4.1" in inconclusive and "+14.1" in inconclusive
    assert "includes zero" in inconclusive
    conclusive = analytics.explain_ab(analytics.ab_test(ab_frame(1_000, 10_000, 1_500, 10_000)))
    assert "does not include zero" in conclusive


def test_ab_test_recovers_the_effect_built_into_the_simulator():
    journeys = synthetic.generate_journeys(n=12_000, seed=42)
    result = analytics.ab_test(journeys, "submitted")
    assert result["ci_low"] <= synthetic.TRUE_AB_LIFT <= result["ci_high"]
    assert result["ci_low"] > 0


def test_ab_test_finds_nothing_when_there_is_no_effect():
    journeys = synthetic.generate_journeys(n=12_000, seed=42, true_ab_lift=0.0)
    result = analytics.ab_test(journeys, "submitted")
    assert result["ci_low"] < 0 < result["ci_high"]


# --- Funnel and partner comparison ------------------------------------------

def stage_frame() -> pd.DataFrame:
    """10 journeys: 4 stop at started, 3 at submitted, 1 at approved, 2 funded."""
    stages = ["started"] * 4 + ["submitted"] * 3 + ["approved"] * 1 + ["funded"] * 2
    return pd.DataFrame({"stage_reached": stages, "partner": ["alder"] * 5 + ["birch"] * 5})


def test_conversion_funnel_counts():
    funnel = analytics.conversion_funnel(stage_frame())
    assert funnel["stage"].tolist() == ["started", "submitted", "approved", "offer_accepted", "funded"]
    assert funnel["journeys"].tolist() == [10, 6, 3, 2, 2]
    assert funnel["share_of_started"].tolist() == pytest.approx([1.0, 0.6, 0.3, 0.2, 0.2])
    assert funnel["step_conversion"].tolist()[1:] == pytest.approx([0.6, 0.5, 2 / 3, 1.0])
    assert math.isnan(funnel["step_conversion"].iloc[0])


def test_conversion_funnel_by_group():
    funnel = analytics.conversion_funnel(stage_frame(), group_by="partner")
    alder = funnel[funnel["partner"] == "alder"]
    birch = funnel[funnel["partner"] == "birch"]
    assert alder["journeys"].tolist() == [5, 1, 0, 0, 0]
    assert birch["journeys"].tolist() == [5, 5, 3, 2, 2]


def test_partner_comparison():
    journeys = pd.DataFrame({
        "partner": ["alder"] * 4 + ["birch"] * 4,
        "funded": [True, True, False, False, True, False, False, False],
        "submitted": [True, True, True, False, True, True, False, False],
        "had_api_error": [False, False, False, True, True, True, False, False],
        "loan_amount": [1_000, 2_000, 3_000, 4_000, 10_000, 20_000, 30_000, 40_000],
        "latency_ms": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0, 800.0],
    })
    table = analytics.partner_comparison(journeys).set_index("partner")
    assert table.loc["alder", "journeys"] == 4
    assert table.loc["alder", "conversion"] == pytest.approx(0.5)
    assert table.loc["birch", "conversion"] == pytest.approx(0.25)
    assert table.loc["alder", "api_error_rate"] == pytest.approx(0.25)
    assert table.loc["birch", "submit_rate"] == pytest.approx(0.5)
    assert table.loc["alder", "latency_ms_p50"] == pytest.approx(250.0)
    assert table.loc["birch", "median_amount"] == pytest.approx(25_000)
    low, high = analytics.wilson_interval(2, 4)
    assert table.loc["alder", "conversion_ci_low"] == pytest.approx(low)
    assert table.loc["alder", "conversion_ci_high"] == pytest.approx(high)


# --- Request-log analytics --------------------------------------------------

def request_frame() -> pd.DataFrame:
    """A hand-made request log: 100 requests on v1, 100 on v2."""
    rows = []

    def add(count, release, partner, band, failed):
        for _ in range(count):
            rows.append({"id": len(rows) + 1, "release_version": release, "partner_id": partner,
                         "amount_band": band, "request": "POST /applications", "failed": failed})

    add(95, "v1", "alder", "under 10k", False)
    add(5, "v1", "alder", "25k-50k", True)
    add(60, "v2", "alder", "under 10k", False)
    add(40, "v2", "birch", "25k-50k", True)
    return pd.DataFrame(rows)


def test_error_rates_by_group():
    table = analytics.error_rates(request_frame(), "release_version").set_index("release_version")
    assert table.loc["v1", ["requests", "failures"]].tolist() == [100, 5]
    assert table.loc["v1", "failure_rate"] == pytest.approx(0.05)
    assert table.loc["v2", "failure_rate"] == pytest.approx(0.40)
    assert table.loc["v1", "ci_low"] < 0.05 < table.loc["v1", "ci_high"]


def test_compare_failure_rates_between_releases():
    result = analytics.compare_failure_rates(request_frame(), "release_version", "v1", "v2")
    assert result["difference"] == pytest.approx(0.35)
    assert result["ci_low"] > 0.2
    assert result["p_value"] < 1e-6


def test_detect_release_changes_flags_a_real_jump_only():
    changes = analytics.detect_release_changes(request_frame())
    assert len(changes) == 1
    row = changes.iloc[0]
    assert (row["from_release"], row["to_release"]) == ("v1", "v2")
    assert row["change"] == pytest.approx(0.35)
    assert row["flagged"]

    steady = request_frame()
    steady["failed"] = [i % 20 == 0 for i in range(len(steady))]   # 5% in both releases
    assert not analytics.detect_release_changes(steady).iloc[0]["flagged"]


def test_failure_concentration_finds_the_disproportionate_segment():
    after = request_frame().query("release_version == 'v2'")
    table, test = analytics.failure_concentration(after, "amount_band")
    top = table.iloc[0]
    assert top["amount_band"] == "25k-50k"
    assert top["share_of_requests"] == pytest.approx(0.4)
    assert top["share_of_failures"] == pytest.approx(1.0)
    assert top["lift"] == pytest.approx(2.5)        # 100% failure rate vs 40% overall
    assert test["overall_failure_rate"] == pytest.approx(0.4)
    assert test["p_value"] < 1e-6


def test_paired_flips_finds_requests_that_changed_outcome():
    def run(label, statuses):
        return pd.DataFrame({
            "run_label": label, "journey": [0, 1, 2], "request": "POST /applications",
            "partner_id": "alder", "product": ["term_loan", "term_loan", "credit_line"],
            "amount": [5_000, 30_000, 40_000], "amount_band": ["under 10k", "25k-50k", "25k-50k"],
            "status_code": statuses, "failure_reason": [None if s < 400 else "rule" for s in statuses],
            "request_id": [f"{label}-{i}" for i in range(3)],
        })

    log = pd.concat([run("before", [201, 201, 422]), run("after", [201, 422, 422])])
    flips = analytics.paired_flips(log, "before", "after")
    assert len(flips) == 1                      # journey 2 failed both times: not a flip
    assert flips.iloc[0]["journey"] == 1
    assert flips.iloc[0]["amount"] == 30_000
    assert flips.iloc[0]["request_id_after"] == "after-1"


def test_load_requests_expands_metadata(client, alder, db_file):
    from tests.helpers import apply, new_customer

    customer = new_customer(client, alder)
    apply(client, alder, customer["id"], product="term_loan", amount=30_000)
    client.get("/applications/999", headers=alder)

    log = analytics.load_requests(db_file)
    assert len(log) == 3
    post = log[log["request"] == "POST /applications"].iloc[0]
    assert (post["product"], post["amount"], post["amount_band"]) == ("term_loan", 30_000, "25k-50k")
    assert log["failed"].tolist() == [False, False, True]


def test_load_requests_on_a_missing_database_is_empty(tmp_path):
    log = analytics.load_requests(tmp_path / "nothing-here.db")
    assert log.empty
    assert {"failed", "request", "amount_band", "product"} <= set(log.columns)


# --- Correlation versus causation -------------------------------------------

def test_naive_comparison_overstates_the_effect_of_errors():
    journeys = synthetic.generate_journeys(n=12_000, seed=42)
    result = analytics.error_dropoff_analysis(journeys)

    truth = result["true_effect"]
    naive = result["naive"]["difference"]
    adjusted = result["adjusted_difference"]

    assert truth == pytest.approx(synthetic.TRUE_ERROR_EFFECT, abs=0.01)
    assert naive < truth - 0.03                      # the raw gap is too big
    assert not (result["naive"]["ci_low"] <= truth <= result["naive"]["ci_high"])
    assert abs(adjusted - truth) < abs(naive - truth)
    assert result["adjusted_ci_low"] <= truth <= result["adjusted_ci_high"]
    assert result["share_of_journeys_covered"] == pytest.approx(1.0)


def test_no_association_is_found_when_errors_have_no_effect_within_strata():
    journeys = synthetic.generate_journeys(n=12_000, seed=42, true_error_effect=0.0)
    result = analytics.error_dropoff_analysis(journeys)
    assert result["true_effect"] == 0.0
    assert result["naive"]["difference"] < -0.03     # still a correlation, from confounding alone
    assert result["adjusted_ci_low"] < 0 < result["adjusted_ci_high"]
