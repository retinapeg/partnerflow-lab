# Prequalification: decision brief

**Decision:** prepare a real controlled experiment; do not authorise commercial
rollout from simulated evidence.

**Business hypothesis:** showing borrowing eligibility earlier may reduce
application friction and improve completion and funded drawdowns. This validation
tests drawdown only; it does not establish that mechanism in real customers.

**Evidence — all simulated:** seed 0 gives DiD **+2.17 pp**
(95% CI **[1.39, 2.94] pp**), versus naive **+6.09 pp**.
The imposed effect is **+2 pp** per arriving customer.

| Scenario | Mean estimate (pp) | Mean bias (pp) | RMSE (pp) | Covered | Coverage | MC 95% interval |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 2.076 | +0.076 | 0.535 | 94/100 | 94.0% | 87.5%–97.2% |
| untreated_trend | 0.114 | -1.886 | 1.960 | 9/100 | 9.0% | 4.8%–16.2% |

**Sensitivity:** both scenarios use seeds 1000–1099 and the same +2 pp effect.
The violation adds an untreated trend scaled by partner traffic, reaching
0.1 pp/day for large partners from day 28; no parameters were tuned to results.
It is distinct from the controlled partner-7 launch. Compare bias and coverage:
an incorrect counterfactual can defeat an otherwise working estimator.

**Limits:** coverage from 100 simulations is uncertain (MC intervals above).
This is not general calibration or real-world causal evidence. Interpretation
requires parallel untreated trends, homogeneous effects, no anticipation or
spillovers, stable measurement and an independently justified launch control.

**Commercial criterion:** incremental risk-adjusted contribution = incremental
revenue − incremental expected credit losses − incremental operating costs −
incremental intervention costs. Every commercial input is **unknown**.
Cannibalisation of existing lending and changes in borrower risk are **unknown**
and must be included; conversion or requested principal alone cannot establish value.

**Recommendation:** agree eligible customers, feasible randomisation, primary
drawdown outcome, economically meaningful effect size, fixed analysis horizon,
complete follow-up and credit-risk guardrails before testing. Measure contribution
and displacement across products. Rollout requires positive incremental contribution
and acceptable risk; simulated results cannot meet those conditions.

[Validation and assumptions](VALIDATION_SUMMARY.md) ·
[Seed-level evidence](results/drawdown_replications.csv) ·
Reproduce from `experiments/`: `./reproduce.sh`.
