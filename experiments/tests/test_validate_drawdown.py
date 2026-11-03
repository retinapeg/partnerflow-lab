import hashlib
import json

import numpy as np
import pandas as pd
import pytest

from analysis import validate_drawdown as validation
from sim.generate import generate, untreated_trend


def test_prespecified_trend_and_unchanged_estimand():
    traffic = np.array([8, 25, 65, 65])
    day = np.array([139, 139, 139, 27])
    assert untreated_trend(traffic, day, 0.1) == pytest.approx([0, .112 * 17 / 57, .112, 0])
    baseline, truth = generate(1000)
    violation, altered = generate(1000, max_slope_pp=0.1)
    assert truth["prequal_effect_drawdown_pp"] == altered["prequal_effect_drawdown_pp"] == 2.0
    assert truth["prequal_start_day"] == altered["prequal_start_day"]
    assert truth["solo_launch_partner"] == altered["solo_launch_partner"] == 7
    assert truth["solo_launch_week"] == altered["solo_launch_week"] == 8
    assert altered["untreated_trend_max_slope_pp_per_day"] == .1
    covariates = ["customer_id", "partner_id", "arrival_day", "prequal_shown", "requested_amount", "sector"]
    pd.testing.assert_frame_equal(baseline[covariates], violation[covariates])
    pd.testing.assert_frame_equal(baseline.query("arrival_day < 28"), violation.query("arrival_day < 28"))
    assert (violation.stage_drawdown >= baseline.stage_drawdown).all()
    assert violation.stage_drawdown.sum() > baseline.stage_drawdown.sum()
    stages = ["stage_data_complete", "stage_offer", "stage_accepted", "stage_drawdown", "repeat_90d"]
    for previous, current in zip(stages, stages[1:]):
        assert (~violation[current] | violation[previous]).all()
    pd.testing.assert_frame_equal(violation, generate(1000, max_slope_pp=.1)[0])
    with pytest.raises(ValueError):
        generate(0, max_slope_pp=.2)


def example_rows():
    return [
        dict(seed=1, estimate_pp=1, truth_pp=2, ci95_lo_pp=0, ci95_hi_pp=2,
             estimation_error_pp=-1, covers_truth=True),
        dict(seed=2, estimate_pp=3, truth_pp=2, ci95_lo_pp=2, ci95_hi_pp=4,
             estimation_error_pp=1, covers_truth=True),
        dict(seed=3, estimate_pp=4, truth_pp=2, ci95_lo_pp=3, ci95_hi_pp=5,
             estimation_error_pp=2, covers_truth=False),
    ]


def test_metrics_against_hand_calculation():
    s = validation.summarise(example_rows())
    assert s["mean_estimate_pp"] == pytest.approx(8 / 3)
    assert s["mean_bias_pp"] == pytest.approx(2 / 3)
    assert s["rmse_pp"] == pytest.approx(np.sqrt(2))
    assert s["covered"] == 2 and s["n"] == 3
    assert s["coverage"] == pytest.approx(2 / 3)
    assert s["coverage_mc_ci95"] == pytest.approx([0.20766, 0.93851], abs=1e-5)


@pytest.mark.parametrize("bad", ["empty", "duplicate", "error", "coverage", "nonfinite", "interval"])
def test_reject_inconsistent_replications(bad):
    rows = example_rows()
    if bad == "empty":
        rows = []
    elif bad == "duplicate":
        rows[1]["seed"] = rows[0]["seed"]
    elif bad == "error":
        rows[0]["estimation_error_pp"] = 0
    elif bad == "coverage":
        rows[0]["covers_truth"] = False
    elif bad == "nonfinite":
        rows[0]["estimate_pp"] = float("nan")
    else:
        rows[0]["ci95_lo_pp"] = 10
    with pytest.raises(ValueError):
        validation.summarise(rows)


def test_replication_calls_existing_estimator_with_prescribed_generator(monkeypatch):
    calls = []
    def estimate(events, outcome):
        calls.append((events, outcome))
        return {"estimate_pp": 3.0, "ci95": [2.0, 4.0]}
    monkeypatch.setattr(validation.did, "estimate", estimate)
    row = validation.replication(1001, "untreated_trend")
    expected, _ = generate(1001, max_slope_pp=.1)
    pd.testing.assert_frame_equal(calls[0][0], expected)
    assert calls[0][1] == "stage_drawdown"
    assert row == dict(scenario="untreated_trend", seed=1001, truth_pp=2.0, estimate_pp=3.0,
                       ci95_lo_pp=2.0, ci95_hi_pp=4.0, estimation_error_pp=1.0, covers_truth=True)


def test_runner_retains_all_seeds_and_writes_reproducible_evidence(monkeypatch, tmp_path):
    calls = []
    def replication(seed, scenario):
        calls.append((seed, scenario))
        return dict(example_rows()[0], seed=seed, scenario=scenario)
    monkeypatch.setattr(validation, "replication", replication)
    evidence = validation.run(tmp_path)
    assert calls == [(s, c) for c in validation.SCENARIOS for s in range(1000, 1100)] + [(0, "baseline")]
    rows = pd.read_csv(tmp_path / "drawdown_replications.csv")
    assert len(rows) == 200 and not rows.duplicated(["scenario", "seed"]).any()
    for summary in evidence["summaries"].values():
        assert summary["n"] == summary["covered"] == 100
    csv_bytes = (tmp_path / "drawdown_replications.csv").read_bytes()
    assert hashlib.sha256(csv_bytes).hexdigest() == evidence["replications_sha256"]
    assert json.loads((tmp_path / "drawdown_validation.json").read_text()) == evidence
    assert (tmp_path / "VALIDATION_SUMMARY.md").read_text() == validation.render_summary(evidence)
    first = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    validation.run(tmp_path)
    assert first == {p.name: p.read_bytes() for p in tmp_path.iterdir()}
