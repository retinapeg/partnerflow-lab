"""Hierarchical Beta-Binomial partner conversion on pre-intervention data.

Only days 0--27 are used: later data mixes baseline rates and interventions.
The Beta prior's mean and concentration are inferred jointly across partners.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Keep numerical/plotting caches local to this repository, not global settings.
# Python execution avoids requiring a working system C++ toolchain for this
# small model. An explicit PYTENSOR_FLAGS setting can opt into compilation.
os.environ.setdefault("PYTENSOR_FLAGS", f"base_compiledir={ROOT / '.cache/pytensor'},cxx=")
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache/matplotlib"))
os.environ.setdefault("NUMBA_CACHE_DIR", str(ROOT / ".cache/numba"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / ".cache"))

import arviz as az
import numpy as np
import pymc as pm


def estimate(events, seed: int = 0, draws: int = 1500, tune: int = 1000) -> dict:
    baseline = events.loc[events.arrival_day < 28]
    if baseline.empty or baseline.prequal_shown.any():
        raise ValueError("Need untreated baseline data from days 0--27")
    partners = baseline.groupby("partner_id").stage_drawdown.agg(["sum", "count"])
    with pm.Model(coords={"partner": partners.index.to_numpy()}):
        mean = pm.Beta("mean", alpha=2, beta=6)
        concentration = pm.Gamma("concentration", alpha=2, beta=0.1)
        rate = pm.Beta("rate", alpha=mean * concentration,
                       beta=(1 - mean) * concentration, dims="partner")
        pm.Binomial("drawdowns", n=partners["count"].to_numpy(), p=rate,
                    observed=partners["sum"].to_numpy(), dims="partner")
        posterior = pm.sample(draws=draws, tune=tune, chains=2, cores=1,
                              random_seed=seed, target_accept=0.95,
                              progressbar=False, return_inferencedata=True)
    samples = posterior.posterior["rate"].stack(sample=("chain", "draw")).values
    result = partners.rename(columns={"count": "n", "sum": "conversions"}).copy()
    result["raw_rate"] = result.conversions / result.n
    result["posterior_mean"] = samples.mean(axis=1)
    result["ci94_lo"], result["ci94_hi"] = np.quantile(samples, [0.03, 0.97], axis=1)
    result["shrinkage"] = result.posterior_mean - result.raw_rate
    mean_samples = samples.mean(axis=0)
    return {
        "partners": result,
        "estimate_pp": float(100 * mean_samples.mean()),
        "ci95": (100 * np.quantile(mean_samples, [0.025, 0.975])).tolist(),
        "diagnostics": {
            "max_rhat": float(az.rhat(posterior).to_array().max()),
            "min_bulk_ess": float(az.ess(posterior, method="bulk").to_array().min()),
            "divergences": int(posterior.sample_stats.diverging.sum()),
        },
    }


def calibration(partners, truth: dict) -> float:
    """Empirical coverage across the 40 known baseline partner rates."""
    true_rates = np.array([truth["partner_base_rate"][str(i)] for i in partners.index])
    return float(((partners.ci94_lo <= true_rates) & (true_rates <= partners.ci94_hi)).mean())
