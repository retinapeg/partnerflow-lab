# Three-minute review: a decision, its evidence, and working software

> Synthetic data. Fictional lending simulation. Not affiliated with iwoca.

## Prepare before the clock starts

1. From `experiments/`, run `./reproduce.sh`. Open
   `PREQUALIFICATION_DECISION_BRIEF.md` and `VALIDATION_SUMMARY.md` in the editor.
   Keep `results/drawdown_replications.csv` available for inspection. The documents
   are generated from actual results; do not type or rehearse invented numbers.
2. From the repository root, run `./start.sh`. Open http://127.0.0.1:8510.
   The experiments API is optional for this walkthrough; its results are shown
   in existing Markdown files, not an unimplemented dashboard.
3. In a **disposable demo lab**, run `.venv/bin/python demo.py` against that API.
   **This resets its recorded data.** It sends the three seeded runs, applies the
   patch and prints observed counts. Use a separate `PARTNERFLOW_DB` path if you
   want to preserve a working lab. The API and dashboard must use the same path;
   the CLI reads evidence over HTTP from the API it called.
4. Open dashboard tabs on **Data science**, **Broker monitoring** and
   **Release simulation**. Use the defaults: journey seed 42 / 12,000 rows;
   broker seed 11 / 14 days / seven-day baseline; release seed 7 / 150 journeys.
5. Run `.venv/bin/python -m pytest -q` in advance and retain its actual output,
   alongside the experiment test output. Do not wait for tests or MCMC on camera.

## The demonstration

| Time | Show or do | Say |
|---|---|---|
| 0:00–0:30 | **Data science**, conversion funnel; then **Broker monitoring**, section 5 | “The question is whether prequalification merits a real controlled experiment. A funnel identifies where customers leave. A low conversion rate may reflect appropriate credit selection. The broker's completion shortfall and requested-amount proxy describe a change; neither establishes lost profit.” |
| 0:30–1:00 | Open **PREQUALIFICATION_DECISION_BRIEF.md**, decision and seed-0 evidence | “All of this evidence is simulated. Prequalification has an imposed +2-point drawdown effect. The existing DiD estimates +2.17 points, with a 95% interval of 1.39 to 2.94. This supports testing an analytical approach and a business hypothesis, not forecasting real uplift.” |
| 1:00–1:50 | Open **VALIDATION_SUMMARY.md**, protocol and two-row table; briefly show the CSV | “The naive estimate is +6.09 points. Size-related rollout and time trends confound it. We kept all seeds 1000–1099 and used the same estimator in both scenarios. Read the actual baseline and violation bias, RMSE and covered-out-of-100 counts here. The violation adds one predeclared untreated trend favouring larger partners; the true treatment effect stays +2. Coverage itself is uncertain. The separate Bayesian model pools baseline partner rates; its single-seed coverage does not validate causal effects.” |
| 1:50–2:30 | **Release simulation**, recorded three-run table and matched failures. In **API playground**, click *Send request* for the default customer, choose `POST /applications`, then *Send request*. Show its response and request ID, the **Broker monitoring** Meridian incident, and saved test output | “This is functioning software. The same 150 cases produced 6, 41 and 16 refusals. The patch restores eligible products while retaining the intended invoice-finance control. Replayed cases provide functional evidence, so there is no population confidence interval. Monitoring also identifies the seeded incident. These are the actual passing API, dashboard and analysis checks.” |
| 2:30–3:00 | Return to the brief's commercial criterion and recommendation | “Recommend a real experiment only after agreeing eligibility, randomisation, outcome follow-up and credit-risk guardrails. Revenue, expected credit losses, operating costs, intervention costs, cannibalisation and borrower-risk changes are unknown. Rollout requires positive incremental risk-adjusted contribution and acceptable risk. Synthetic evidence cannot authorise it.” |

The release runs are prepared before filming; the playground requests are sent live.
Do not imply that replayed results are a live randomised customer experiment.

## Optional technical follow-up, outside the three minutes

- **Release mechanics:** matched requests locate R-204's missing product check;
  patching it retains the intended invoice-finance restriction. Inspect
  `partnerflow/rules.py` and `tests/test_release.py`.
- **Causal assumptions:** homogeneous effects, parallel untreated trends,
  no anticipation or spillovers, stable composition/measurement, and a justified
  partner-7 launch control. The violation is deliberately omitted from the fitted model.
- **Bayesian work:** the generated methods table reports baseline partial pooling,
  credible intervals and sampler diagnostics. It is not a partner treatment-effect model.
- **Reproducibility:** `experiments/results/` stores the seed-level results and
  hashes; `cd experiments && ./reproduce.sh` regenerates them without manual repairs.
- **Operational fallback:** `.venv/bin/python demo.py` demonstrates the release
  story against a running API; it does not run the scientific validation.
