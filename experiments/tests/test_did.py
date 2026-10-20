import pytest

from analysis.did import estimate


@pytest.mark.parametrize("outcome,key,label", [
    ("stage_data_complete", "prequal_effect_data_complete_pp", "completion"),
    ("stage_drawdown", "prequal_effect_drawdown_pp", "drawdown"),
])
def test_did_recovers_prequal(simulation, record_result, outcome, key, label):
    events, truth = simulation
    result = estimate(events, outcome)
    effect = truth[key]
    # Tolerance: 1pp for each pre-qualification effect with 40 partner clusters.
    assert abs(result["estimate_pp"] - effect) < 1.0
    assert result["ci95"][0] <= effect <= result["ci95"][1]
    assert result["n_partners"] == 40
    assert result["p_value"] < 0.05
    record_result(f"TWFE DiD: {label}", result["estimate_pp"], result["ci95"], effect)
