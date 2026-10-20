# PLAN.md — partner-flow-lab

## Goal tier (this session; STOP when all are ticked)
- [x] `sim/generate.py`: 40 partners, Beta-distributed base rates, Poisson arrivals, stages per SCHEMA.md, intervention A (staggered pre-qualification; rollout order correlated with partner size), intervention B (top-up notification from day 70, delayed 90-day outcome), weekly seasonality, week-14 shock, partner 7 solo launch week 8. Writes events.parquet + truth.json.
- [x] `analysis/power.py`: two-proportion power calc → n per arm and days.
- [x] `analysis/ab.py`: randomised A/B analysis (lift, CI, p, Bayesian P(better) via Beta posteriors).
- [x] `analysis/naive.py`: before/after on pre-qualification (expected to be wrong; test asserts bias).
- [x] `analysis/did.py`: two-way fixed-effects DiD with staggered adoption (statsmodels), clustered SEs by partner.
- [x] `analysis/hier.py`: PyMC hierarchical Beta-Binomial (or logit random effects) of partner conversion; posterior per partner; calibration = share of partners whose true rate falls in the 94% interval.
- [x] `api/`: `/v1/funnel/{id}`, `/v1/experiments/power`, `/v1/experiments/analyse`, `/health`; pydantic models; schema tests.
- [x] `tests/`: each estimator vs truth.json; endpoint tests with TestClient.
- [x] `analysis/report.py` → README.md table: method | estimate | 95% interval | truth | bias.
- [x] README.md: "Learning" paragraph and ML/DS learning log (requested by Leo in this session), how to run, the table.

## Moonshot (next session)
- [ ] `analysis/its.py` interrupted time series on the week-14 shock.
- [ ] `analysis/synth.py` synthetic control for partner 7 (donor pool = others, placebo-in-space).
- [ ] `/v1/partners/{id}/posterior`, `/metrics` middleware (counts, p50/p95, error rate).
- [ ] `app/dashboard.py` Streamlit reading the API: funnel, results table, calibration plot, power form.
- [ ] Deploy: API on Render/Fly, dashboard on Streamlit Community Cloud; URLs in README.
- [ ] CHANGELOG.md as partner-facing release notes (what changed, who it affects, migration, deprecation date).

## Mars shot
- [ ] Replace repeat_90d with a survival model of CLtV on a public loan-performance dataset.

## Division of labour
Claude Code #1: sim + analysis + tests. Codex: api + app against SCHEMA.md. Claude Code #2: review frozen diffs of both before merge. Leo: reads the table, writes the Learning paragraph, decides what ships.
