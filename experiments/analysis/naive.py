"""Deliberately confounded pooled before/after comparison of staggered adoption."""

import math

from scipy.stats import norm


def estimate(events, outcome: str = "stage_drawdown") -> dict:
    before = events.loc[~events.prequal_shown, outcome]
    after = events.loc[events.prequal_shown, outcome]
    if min(len(before), len(after)) < 2:
        raise ValueError("Need at least two observations before and after adoption")
    lift = 100 * (after.mean() - before.mean())
    se = 100 * math.sqrt(before.var() / len(before) + after.var() / len(after))
    radius = norm.ppf(0.975) * se
    return {"estimate_pp": float(lift), "ci95": [float(lift - radius), float(lift + radius)]}
