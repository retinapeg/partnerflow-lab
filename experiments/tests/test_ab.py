import pytest

from analysis.ab import analyse, analyse_topup


def test_ab_recovers_topup(simulation, record_result):
    events, truth = simulation
    result = analyse_topup(events)
    effect = truth["topup_effect_repeat_90d_pp"]
    # Sampling tolerance: 1 percentage point around the known 4pp effect.
    assert abs(result["lift_pp"] - effect) < 1.0
    assert result["ci95"][0] <= effect <= result["ci95"][1]
    assert result["p_value"] < 0.05
    assert result["prob_treatment_better"] > 0.95
    record_result("Randomised A/B: repeat", result["lift_pp"], result["ci95"], effect)


@pytest.mark.parametrize("k", [0, 5, 10])
def test_equal_arms_and_boundary_intervals(k):
    result = analyse({"n": 10, "conversions": k}, {"n": 10, "conversions": k}, 10)
    assert result["lift_pp"] == 0
    assert result["p_value"] == 1
    assert result["prob_treatment_better"] == pytest.approx(0.5)
    assert result["ci95"][0] < 0 < result["ci95"][1]
    assert result["ready_to_read"]
    assert not analyse({"n": 10, "conversions": k}, {"n": 10, "conversions": k}, 11)["ready_to_read"]


@pytest.mark.parametrize("arm", [
    {"n": 0, "conversions": 0}, {"n": 10, "conversions": 11},
    {"n": 10, "conversions": -1}, {"n": 10.5, "conversions": 1},
])
def test_invalid_counts(arm):
    with pytest.raises(ValueError):
        analyse(arm, {"n": 10, "conversions": 1})
