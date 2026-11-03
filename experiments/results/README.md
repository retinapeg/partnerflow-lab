# Reproducible evidence

Run `./reproduce.sh` from `experiments/`. Do not edit numerical results by hand.

- `method-results.json`: passing seed-0 estimator tests, used by `analysis.report`
  to generate the README table. `estimation_error` is estimate minus truth for
  one run; it is not repeated-sampling bias.
- `drawdown_replications.csv`: all 200 scenario/seed combinations, truth, estimate,
  confidence interval, estimation error and inclusive coverage flag.
- `drawdown_validation.json`: fixed protocol, aggregate metrics, seed-0 comparison,
  dependency versions and source/CSV hashes. Bias and RMSE are in percentage points;
  coverage is a fraction. Coverage uncertainty uses a Wilson Monte Carlo interval.

Sources are hashed by content so provenance works in an uncommitted checkout as
well as a clone. Numerical libraries/platforms can change floating-point rounding;
the locked environment and fixed seeds define the reproduction used here.
No real customers, real commercial inputs or real-world causal effects are present.
