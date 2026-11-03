# Prequalification drawdown validation

All results are simulated. Reproduce from `experiments/` with `./reproduce.sh`;
the focused validation alone is `.venv/bin/python -m analysis.validate_drawdown`.
The existing arrival-weighted TWFE estimator with partner-clustered standard
errors is unchanged. The estimand is the homogeneous marginal drawdown effect
per arriving customer, imposed at +2 pp in both scenarios.

## Protocol fixed before evaluation

- Exactly 100 seeds: 1000–1099, all retained in both scenarios. The same seed
  uses the same arrivals, adoption schedule and random draws across scenarios.
- Baseline: existing generator, 40 partners, 140 arrival days, staggered
  adoption correlated with size, seasonality, common trend and week-14 shock.
- Violation: add **one untreated differential trend**:
  `0.001 * (partner_daily_traffic - 8) / 57 * max(day - 27, 0)` to the marginal
  stage probabilities. Daily traffic is 8/25/65 for small/medium/large partners.
  Slopes are 0/0.02982456/0.1 pp/day; maximum accumulated shift is 11.2 pp.
  These parameters were chosen before evaluating the 100 seeds and not tuned.
  The trend exists in untreated potential outcomes; it is not a treatment effect.
- The original partner-7 +3 pp launch on day 56 remains in both scenarios and
  is controlled by the existing estimator. The new trend is deliberately omitted
  from that estimator. No other generator parameter changes; no clipping of
  probabilities is used, so the imposed treatment effect remains +2 pp.

## Results

| Scenario | Mean estimate (pp) | Mean bias (pp) | RMSE (pp) | Covered | Coverage | MC 95% interval |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 2.076 | +0.076 | 0.535 | 94/100 | 94.0% | 87.5%–97.2% |
| untreated_trend | 0.114 | -1.886 | 1.960 | 9/100 | 9.0% | 4.8%–16.2% |

Bias is the mean of estimate minus truth across replications. RMSE is the
square root of mean squared estimation error. Coverage includes interval endpoints.
The MC interval is a Wilson interval for repeated-simulation coverage, **not** a
treatment-effect interval. With only 100 seeds, coverage is itself uncertain.
These results do not establish general calibration or validity on real data.

Seed 0 reproduces DiD **2.17 pp** (95% CI
**[1.39, 2.94] pp**) and naive **6.09 pp**.
One-run estimate minus truth is estimation error, not a measurement of estimator bias.

## Interpretation and assumptions

Compare both rows: changing only the untreated trend tests sensitivity of the
same estimator to a known identification failure. Common day effects cannot
absorb different partner trends correlated with adoption. A narrow interval
does not protect against bias from an invalid counterfactual.

Causal interpretation needs parallel untreated trends conditional on the model,
no anticipation or spillovers, a stable population and measurement process,
and the homogeneous treatment effects assumed by this staggered TWFE design.
The launch control must be justified independently in real data. All partners
eventually adopt; identification comes from variation in adoption timing.
This exercise validates drawdown only. The separate Bayesian baseline model's
single-seed coverage is not repeated-simulation calibration.

## Provenance

`results/drawdown_replications.csv` retains every seed, truth, estimate, interval,
error and coverage flag. `results/drawdown_validation.json` contains this protocol,
summaries, seed-0 checks, dependency versions, and SHA-256 hashes of the generator,
estimator, runner, lockfile and CSV. Generated results contain no timestamps.
See [the decision brief](PREQUALIFICATION_DECISION_BRIEF.md) for the commercial decision.
