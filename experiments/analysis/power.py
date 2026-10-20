"""Equal-allocation, two-sided normal approximation for two proportions."""

import math

from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize


def calculate_power(baseline_rate: float, mde_pp: float, daily_traffic: float,
                    alpha: float = 0.05, power: float = 0.8) -> dict:
    values = (baseline_rate, mde_pp, daily_traffic, alpha, power)
    if not all(math.isfinite(v) for v in values):
        raise ValueError("Inputs must be finite")
    treatment = baseline_rate + mde_pp / 100
    if not (0 < baseline_rate < treatment < 1):
        raise ValueError("Baseline and baseline + positive MDE must be between 0 and 1")
    if not (0 < alpha < power < 1) or daily_traffic <= 0:
        raise ValueError("Require 0 < alpha < power < 1 and positive total eligible daily traffic")
    effect = abs(proportion_effectsize(baseline_rate, treatment))
    n = math.ceil(NormalIndPower().solve_power(effect, alpha=alpha, power=power, ratio=1))
    return {"n_per_arm": n, "days_to_run": math.ceil(2 * n / daily_traffic)}
