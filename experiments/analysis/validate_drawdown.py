"""Fixed 100-seed validation of the existing prequalification drawdown DiD.

Protocol fixed before running: seeds 1000..1099, max untreated trend slope
0.1 percentage points/day, starting day 28 and scaled by baseline traffic.
No seed selection, parameter search, additional outcomes or new estimators.
"""

import argparse
import hashlib
import importlib.metadata
import json
import platform
from pathlib import Path

import numpy as np
import pandas as pd
from statsmodels.stats.proportion import proportion_confint

from analysis import did, naive
from sim.generate import generate

ROOT = Path(__file__).resolve().parents[1]
SEEDS = tuple(range(1000, 1100))
SCENARIOS = {"baseline": 0.0, "untreated_trend": 0.1}


def replication(seed: int, scenario: str) -> dict:
    events, truth = generate(seed, max_slope_pp=SCENARIOS[scenario])
    result = did.estimate(events, "stage_drawdown")
    effect = truth["prequal_effect_drawdown_pp"]
    lo, hi = result["ci95"]
    return {
        "scenario": scenario, "seed": seed, "truth_pp": effect,
        "estimate_pp": result["estimate_pp"], "ci95_lo_pp": lo, "ci95_hi_pp": hi,
        "estimation_error_pp": result["estimate_pp"] - effect,
        "covers_truth": bool(lo <= effect <= hi),
    }


def summarise(rows: list[dict]) -> dict:
    if not rows or len({r["seed"] for r in rows}) != len(rows):
        raise ValueError("Need nonempty replications with distinct seeds")
    values = np.array([[r[k] for k in (
        "estimate_pp", "truth_pp", "ci95_lo_pp", "ci95_hi_pp", "estimation_error_pp"
    )] for r in rows], dtype=float)
    estimates, truths, lo, hi, errors = values.T
    if not np.isfinite(values).all() or (lo > hi).any():
        raise ValueError("Nonfinite results or reversed intervals")
    if not np.allclose(errors, estimates - truths, atol=1e-12, rtol=0):
        raise ValueError("Estimation errors must equal estimate minus truth")
    covered = (lo <= truths) & (truths <= hi)
    if not np.array_equal(covered, [r["covers_truth"] for r in rows]):
        raise ValueError("Coverage flags disagree with intervals")
    count, n = int(covered.sum()), len(rows)
    interval = proportion_confint(count, n, alpha=0.05, method="wilson")
    return {
        "n": n, "mean_estimate_pp": float(estimates.mean()),
        "mean_bias_pp": float(errors.mean()), "rmse_pp": float(np.sqrt(np.mean(errors ** 2))),
        "covered": count, "coverage": count / n,
        "coverage_mc_ci95": [float(x) for x in interval],
    }


def summary_table(summaries: dict) -> str:
    lines = [
        "| Scenario | Mean estimate (pp) | Mean bias (pp) | RMSE (pp) | Covered | Coverage | MC 95% interval |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for scenario, s in summaries.items():
        lo, hi = s["coverage_mc_ci95"]
        lines.append(
            f"| {scenario} | {s['mean_estimate_pp']:.3f} | {s['mean_bias_pp']:+.3f} | "
            f"{s['rmse_pp']:.3f} | {s['covered']}/{s['n']} | {s['coverage']:.1%} | {lo:.1%}–{hi:.1%} |"
        )
    return "\n".join(lines)


def render_summary(evidence: dict) -> str:
    single = evidence["seed_0"]
    lo, hi = single["ci95_lo_pp"], single["ci95_hi_pp"]
    return f"""# Prequalification drawdown validation

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

{summary_table(evidence['summaries'])}

Bias is the mean of estimate minus truth across replications. RMSE is the
square root of mean squared estimation error. Coverage includes interval endpoints.
The MC interval is a Wilson interval for repeated-simulation coverage, **not** a
treatment-effect interval. With only 100 seeds, coverage is itself uncertain.
These results do not establish general calibration or validity on real data.

Seed 0 reproduces DiD **{single['estimate_pp']:.2f} pp** (95% CI
**[{lo:.2f}, {hi:.2f}] pp**) and naive **{single['naive_estimate_pp']:.2f} pp**.
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
"""


def render_brief(evidence: dict) -> str:
    s = evidence["seed_0"]
    lo, hi = s["ci95_lo_pp"], s["ci95_hi_pp"]
    return f"""# Prequalification: decision brief

**Decision:** prepare a real controlled experiment; do not authorise commercial
rollout from simulated evidence.

**Business hypothesis:** showing borrowing eligibility earlier may reduce
application friction and improve completion and funded drawdowns. This validation
tests drawdown only; it does not establish that mechanism in real customers.

**Evidence — all simulated:** seed 0 gives DiD **+{s['estimate_pp']:.2f} pp**
(95% CI **[{lo:.2f}, {hi:.2f}] pp**), versus naive **+{s['naive_estimate_pp']:.2f} pp**.
The imposed effect is **+2 pp** per arriving customer.

{summary_table(evidence['summaries'])}

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
"""


def run(output: Path = ROOT / "results") -> dict:
    rows = []
    for scenario in SCENARIOS:
        for index, seed in enumerate(SEEDS, 1):
            rows.append(replication(seed, scenario))
            if index % 10 == 0:
                print(f"{scenario}: {index}/{len(SEEDS)}", flush=True)
    summaries = {s: summarise([r for r in rows if r["scenario"] == s]) for s in SCENARIOS}
    single = replication(0, "baseline")
    single["naive_estimate_pp"] = naive.estimate(generate(0)[0])["estimate_pp"]
    sources = ("sim/generate.py", "analysis/did.py", "analysis/naive.py",
               "analysis/validate_drawdown.py", "uv.lock")
    evidence = {
        "protocol": {"seeds": list(SEEDS), "outcome": "stage_drawdown",
                     "scenarios_max_slope_pp_per_day": SCENARIOS,
                     "trend_formula": "max_slope_pp/100 * (traffic-8)/57 * max(day-27,0)",
                     "effect_source": "truth.prequal_effect_drawdown_pp"},
        "summaries": summaries, "seed_0": single,
        "python": platform.python_version(),
        "versions": {p: importlib.metadata.version(p) for p in ("numpy", "pandas", "scipy", "statsmodels")},
        "source_sha256": {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources},
    }
    output.mkdir(parents=True, exist_ok=True)
    csv = output / "drawdown_replications.csv"
    pd.DataFrame(rows).to_csv(csv, index=False)
    evidence["replications_sha256"] = hashlib.sha256(csv.read_bytes()).hexdigest()
    (output / "drawdown_validation.json").write_text(json.dumps(evidence, indent=2, allow_nan=False) + "\n")
    # Keep summary beside its repository brief by default; isolated runs stay isolated.
    summary = ROOT / "VALIDATION_SUMMARY.md" if output == ROOT / "results" else output / "VALIDATION_SUMMARY.md"
    summary.write_text(render_summary(evidence))
    (summary.parent / "PREQUALIFICATION_DECISION_BRIEF.md").write_text(render_brief(evidence))
    print(summary_table(summaries), flush=True)
    return evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "results")
    run(parser.parse_args().output)
