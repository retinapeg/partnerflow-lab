"""Equal-allocation, two-sided normal approximation for two proportions."""

import math
import warnings

from statsmodels.stats.power import NormalIndPower
from statsmodels.stats.proportion import proportion_effectsize
from statsmodels.tools.sm_exceptions import ConvergenceWarning


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
    if not math.isfinite(effect) or effect <= 0:
        raise ValueError("MDE is too small to distinguish numerically")
    solver = NormalIndPower()
    try:
        # statsmodels can warn and return its initial guess after nonconvergence.
        # Such a value is not a sample-size estimate.
        with warnings.catch_warnings():
            warnings.simplefilter("error", ConvergenceWarning)
            warnings.simplefilter("error", RuntimeWarning)
            sample_size = solver.solve_power(effect, alpha=alpha, power=power, ratio=1)
            if not math.isfinite(sample_size) or sample_size <= 0:
                raise ValueError("Nonfinite or nonpositive sample size")
            achieved = solver.power(effect, sample_size, alpha=alpha, ratio=1)
            if not math.isfinite(achieved) or not math.isclose(achieved, power, abs_tol=1e-7):
                raise ValueError("Solver did not achieve the requested power")
            n = math.ceil(sample_size)
            days = 2 * n / daily_traffic
            if not math.isfinite(days) or days <= 0:
                raise ValueError("Nonfinite or nonpositive recruitment time")
            return {"n_per_arm": n, "days_to_run": math.ceil(days)}
    except (ConvergenceWarning, RuntimeWarning, ArithmeticError, ValueError) as exc:
        raise ValueError("Power calculation is numerically unsupported for these inputs; "
                         "use a larger MDE or less extreme design inputs") from exc
