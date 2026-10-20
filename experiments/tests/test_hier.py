import numpy as np

from analysis.hier import calibration, estimate


def test_hierarchy_recovers_baseline_and_calibrates(simulation, record_result):
    events, truth = simulation
    result = estimate(events, seed=0)
    partners = result["partners"]
    true_rates = np.array([truth["partner_base_rate"][str(i)] for i in partners.index])
    coverage = calibration(partners, truth)
    assert len(partners) == 40
    # Partner RMSE tolerance: 3.5pp; aggregate baseline tolerance: 1pp.
    assert np.sqrt(np.mean((partners.posterior_mean - true_rates) ** 2)) < 0.035
    assert abs(result["estimate_pp"] - 100 * true_rates.mean()) < 1.0
    # 40 intervals give coarse coverage increments of 2.5pp; allow 80--100%.
    assert 0.8 <= coverage <= 1.0
    assert (partners.ci94_lo < partners.ci94_hi).all()
    assert (partners.ci94_lo.between(0, 1) & partners.ci94_hi.between(0, 1)).all()
    assert result["diagnostics"]["max_rhat"] < 1.05
    assert result["diagnostics"]["min_bulk_ess"] > 300
    assert result["diagnostics"]["divergences"] == 0
    record_result("Hierarchical: mean baseline", result["estimate_pp"], result["ci95"],
                  100 * true_rates.mean(),
                  {"calibration94": coverage, **result["diagnostics"]})
