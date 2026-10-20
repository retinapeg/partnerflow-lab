"""Fixed-horizon randomised comparison; uniform Beta priors for each arm."""

import math
from numbers import Integral

from scipy.integrate import quad
from scipy.stats import beta, norm
from statsmodels.stats.proportion import proportion_confint


def analyse(control: dict, treatment: dict, planned_n_per_arm: int | None = None) -> dict:
    for arm in (control, treatment):
        n, k = arm["n"], arm["conversions"]
        if any(isinstance(v, bool) or not isinstance(v, Integral) for v in (n, k)):
            raise ValueError("Counts must be integers")
        if n <= 0 or not 0 <= k <= n:
            raise ValueError("Require n > 0 and 0 <= conversions <= n")
    if planned_n_per_arm is not None and (
        isinstance(planned_n_per_arm, bool) or not isinstance(planned_n_per_arm, Integral)
        or planned_n_per_arm <= 0
    ):
        raise ValueError("Planned sample size must be a positive integer")
    nc, nt = control["n"], treatment["n"]
    kc, kt = control["conversions"], treatment["conversions"]
    pc, pt = kc / nc, kt / nt
    lift = pt - pc
    lc, uc = proportion_confint(kc, nc, method="wilson")
    lt, ut = proportion_confint(kt, nt, method="wilson")
    # Newcombe's Wilson-based interval also behaves sensibly at 0/n and n/n.
    ci = [lift - math.hypot(pt - lt, uc - pc), lift + math.hypot(ut - pt, pc - lc)]
    pooled = (kc + kt) / (nc + nt)
    se = math.sqrt(pooled * (1 - pooled) * (1 / nc + 1 / nt))
    p_value = float(2 * norm.sf(abs(lift / se))) if se else 1.0
    cpost, tpost = beta(kc + 1, nc - kc + 1), beta(kt + 1, nt - kt + 1)
    # Transform to a uniform integration variable to avoid missing narrow peaks.
    probability = quad(lambda u: cpost.cdf(tpost.ppf(u)), 0, 1, epsabs=1e-8)[0]
    ready = planned_n_per_arm is not None and min(nc, nt) >= planned_n_per_arm
    note = "Fixed-horizon analysis; counts must contain fully observed outcomes. "
    if planned_n_per_arm is None:
        note += "No planned sample size supplied; readiness is unverified."
    elif not ready:
        note += "Planned sample size has not been reached in both arms."
    else:
        note += "Planned sample size reached; outcome maturity must still be checked."
    return {
        "lift_pp": 100 * lift, "ci95": [100 * float(x) for x in ci],
        "p_value": p_value, "prob_treatment_better": float(probability),
        "ready_to_read": ready, "note": note,
    }


def analyse_topup(events, planned_n_per_arm: int | None = None) -> dict:
    eligible = events.loc[events.stage_drawdown & (events.arrival_day >= 70)]
    arms = []
    for notified in (False, True):
        arm = eligible.loc[eligible.topup_notified == notified, "repeat_90d"]
        arms.append({"n": len(arm), "conversions": int(arm.sum())})
    return analyse(*arms, planned_n_per_arm=planned_n_per_arm)
