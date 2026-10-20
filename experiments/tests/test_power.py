import math

import numpy as np
import pytest
from scipy.stats import norm

from analysis.power import calculate_power


def test_power_at_true_effect(simulation):
    events, truth = simulation
    eligible = events.query("stage_drawdown and arrival_day >= 70")
    baseline = eligible.loc[~eligible.topup_notified, "repeat_90d"].mean()
    delta = truth["topup_effect_repeat_90d_pp"] / 100
    traffic = len(eligible) / 70
    result = calculate_power(baseline, delta * 100, traffic)
    n = result["n_per_arm"]
    assert result["days_to_run"] == math.ceil(2 * n / traffic)
    rng = np.random.default_rng(0)
    control = rng.binomial(n, baseline, 20_000)
    treated = rng.binomial(n, baseline + delta, 20_000)
    pooled = (control + treated) / (2 * n)
    z = (treated - control) / n / np.sqrt(pooled * (1 - pooled) * 2 / n)
    # Monte Carlo verification: achieved power within 3 percentage points of 80%.
    assert abs(np.mean(np.abs(z) > norm.ppf(0.975)) - 0.8) < 0.03
    assert calculate_power(baseline, delta * 50, traffic)["n_per_arm"] > n


@pytest.mark.parametrize("overrides", [
    {"baseline_rate": 0}, {"mde_pp": 0}, {"mde_pp": 90},
    {"daily_traffic": 0}, {"alpha": 0.9}, {"power": 1}, {"mde_pp": float("nan")},
])
def test_invalid_power(overrides):
    inputs = dict(baseline_rate=0.2, mde_pp=4, daily_traffic=100)
    inputs.update(overrides)
    with pytest.raises(ValueError):
        calculate_power(**inputs)
