"""Two-way fixed effects for homogeneous staggered treatment effects.

Partner-day means are weighted by arrivals. Partner effects absorb stable
differences, day effects absorb common trends/seasonality/shocks, and a known
partner-7 launch indicator separates its local change from pre-qualification.
Clustered standard errors allow dependence within each partner.
"""

import statsmodels.formula.api as smf


def estimate(events, outcome: str = "stage_drawdown") -> dict:
    panel = events.groupby(["partner_id", "arrival_day"], as_index=False).agg(
        rate=(outcome, "mean"), n=(outcome, "size"), treated=("prequal_shown", "first"),
    )
    if panel.partner_id.nunique() < 2 or panel.treated.nunique() < 2:
        raise ValueError("Need multiple partners and both untreated and treated observations")
    panel["treated"] = panel.treated.astype(int)
    panel["solo_launch"] = ((panel.partner_id == 7) & (panel.arrival_day >= 56)).astype(int)
    fit = smf.wls("rate ~ treated + solo_launch + C(partner_id) + C(arrival_day)",
                  data=panel, weights=panel.n).fit(
        cov_type="cluster", cov_kwds={"groups": panel.partner_id}, use_t=True,
    )
    return {
        "estimate_pp": float(100 * fit.params["treated"]),
        "ci95": (100 * fit.conf_int().loc["treated"]).tolist(),
        "p_value": float(fit.pvalues["treated"]),
        "n_partners": int(panel.partner_id.nunique()),
    }
