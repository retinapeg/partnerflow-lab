"""All outcomes are simulated; arrivals end on day 139, follow-up on day 229.

Stage probabilities are nested marginal thresholds on one uniform draw. This
keeps funnel order intact while imposing exactly +6pp completion / +2pp drawdown.
Baseline drawdown rates refer to days 0--27 (mean time effect exactly zero).
"""

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def generate(seed: int = 0) -> tuple[pd.DataFrame, dict]:
    rng = np.random.default_rng(seed)
    size = np.array(["small"] * 20 + ["medium"] * 12 + ["large"] * 8)
    traffic = np.array([8.0] * 20 + [25.0] * 12 + [65.0] * 8)
    base = 0.12 + 0.28 * rng.beta(2, 4, 40)
    # Larger partners adopt first; seeded jitter provides within-size variation.
    order = np.argsort(-(traffic + rng.uniform(0, 6, 40)))
    starts = np.empty(40, dtype=int)
    starts[order] = 28 + (np.arange(40) // 4) * 7
    partner, day = np.meshgrid(np.arange(40), np.arange(140), indexing="ij")
    counts = rng.poisson(traffic[:, None] * (1 + 0.18 * np.sin(2 * np.pi * day / 7)))
    p = np.repeat(partner.ravel(), counts.ravel())
    d = np.repeat(day.ravel(), counts.ravel())
    n = len(p)
    prequal = d >= starts[p]
    # Arrival seasonality weights sine days unevenly; subtract its exact weighted
    # mean so partner_base_rate remains the expected pre-intervention conversion.
    seasonal = 0.012 * (np.sin(2 * np.pi * d / 7) - 0.09)
    # Trend begins after the baseline window, confounding a before/after analysis.
    time = seasonal + 0.0011 * np.maximum(d - 27, 0)
    shock = -0.05 * (d >= 14 * 7)
    solo = 0.03 * ((p == 7) & (d >= 8 * 7))
    draw_rate = base[p] + time + shock + solo + 0.02 * prequal
    complete_rate = 0.62 + 0.35 * (base[p] - 0.2) + time + shock + solo + 0.06 * prequal
    u = rng.random(n)
    draw = u < draw_rate
    accepted = u < draw_rate + 0.09
    offer = u < draw_rate + 0.21
    complete = u < complete_rate
    # Eligibility is determined before randomisation; all eligible controls and
    # treatments receive complete 90-day follow-up in this retrospective dataset.
    eligible = draw & (d >= 70)
    notified = eligible & (rng.random(n) < 0.5)
    repeat = draw & (rng.random(n) < (0.16 + 0.04 * notified))
    events = pd.DataFrame({
        "customer_id": np.arange(n), "partner_id": p, "partner_size": size[p],
        "arrival_day": d, "week": d // 7,
        "requested_amount": np.round(rng.lognormal(np.log(18000), 0.6, n), 2),
        "trading_age_months": rng.integers(6, 180, n),
        "sector": rng.choice(["retail", "services", "manufacturing"], n),
        "prequal_shown": prequal, "topup_notified": notified,
        "stage_data_complete": complete,
        "routed_auto": complete & (rng.random(n) < 0.72),
        "stage_offer": offer, "stage_accepted": accepted,
        "stage_drawdown": draw, "repeat_90d": repeat,
    })
    truth = {
        "prequal_effect_data_complete_pp": 6.0,
        "prequal_effect_drawdown_pp": 2.0,
        "topup_effect_repeat_90d_pp": 4.0,
        "shock_week": 14, "shock_effect_pp": -5.0,
        "solo_launch_partner": 7, "solo_launch_week": 8,
        "prequal_start_day": {str(i): int(starts[i]) for i in range(40)},
        "partner_base_rate": {str(i): float(base[i]) for i in range(40)},
    }
    return events, truth


def write_data(seed: int = 0, output: Path = ROOT / "data") -> tuple[pd.DataFrame, dict]:
    events, truth = generate(seed)
    output.mkdir(parents=True, exist_ok=True)
    events.to_parquet(output / "events.parquet", index=False)
    (output / "truth.json").write_text(json.dumps(truth, indent=2) + "\n")
    return events, truth


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--output", type=Path, default=ROOT / "data")
    args = parser.parse_args()
    events, _ = write_data(args.seed, args.output)
    print(f"Wrote {len(events):,} simulated journeys to {args.output}")
