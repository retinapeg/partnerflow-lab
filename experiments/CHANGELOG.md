# Changelog

## Unreleased — scientific validation and reproducibility

- Repaired the locked project identity using `uv lock`, retaining every dependency version.
- Added `reproduce.sh` and compact versioned evidence for the existing methods table.
- Validated the unchanged drawdown DiD over seeds 1000–1099 under the original
  generator and one predeclared untreated differential trend; retained all 200
  replications, aggregate bias/RMSE/coverage and Monte Carlo coverage uncertainty.
- Added source/data hashes, a generated validation summary and a one-page
  prequalification decision brief. No real-world commercial effect is claimed.
- Renamed single-run table errors to distinguish them from repeated-sampling bias.
- Changed the operational release replay to descriptive regression evidence,
  clarified commercial proxies and funnel prioritisation, and reordered the demo
  around the decision and its scientific evidence.

## 0.1.0 — GOAL tier

- Added the reproducible synthetic partner funnel and ground-truth parameters.
- Added two-proportion sample-size planning, checked by seeded repeated experiments.
- Added randomised top-up analysis with Wilson-based intervals and Beta-posterior comparison.
- Added a deliberately biased before/after benchmark for pre-qualification.
- Added arrival-weighted partner/day fixed effects with partner-clustered uncertainty.
- Added hierarchical baseline conversion posteriors, coverage and sampling diagnostics.
- Added funnel, power, A/B analysis and health endpoints with Pydantic contracts and API tests.
- Added a methods-versus-truth report generated from passing test evidence, plus run instructions and an ML/DS learning log.
- Added architecture snapshots and instructions for inspecting Git checkpoints.
# Bounded numerical correction

- Power calculations now reject indistinguishable rates, solver failures and
  nonfinite recruitment times. The API returns a documented 422 instead of a
  misleading sample size or unhandled error; SCHEMA.md records this behaviour.

