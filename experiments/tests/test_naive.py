import pytest

from analysis.naive import estimate


@pytest.mark.parametrize("outcome,key,label", [
    ("stage_data_complete", "prequal_effect_data_complete_pp", "completion"),
    ("stage_drawdown", "prequal_effect_drawdown_pp", "drawdown"),
])
def test_naive_is_biased(simulation, record_result, outcome, key, label):
    events, truth = simulation
    result = estimate(events, outcome)
    effect = truth[key]
    # Deliberately wrong method: require at least 1.5pp bias, not truth recovery.
    assert abs(result["estimate_pp"] - effect) > 1.5
    assert not result["ci95"][0] <= effect <= result["ci95"][1]
    record_result(f"Naive before/after: {label}", result["estimate_pp"], result["ci95"], effect)
