# SCHEMA.md — agreed contracts (change only by agreement)

## data/events.parquet (one row per customer journey)
| column | type | notes |
|---|---|---|
| customer_id | int | unique |
| partner_id | int | 0..39 |
| partner_size | str | small / medium / large |
| arrival_day | int | 0..139 (20 weeks) |
| week | int | arrival_day // 7 |
| requested_amount | float | GBP |
| trading_age_months | int | |
| sector | str | |
| prequal_shown | bool | intervention A (staggered by partner from `prequal_start_day[partner_id]`) |
| topup_notified | bool | intervention B, from day 70, eligible customers only |
| stage_data_complete | bool | |
| routed_auto | bool | True = automatic decision, False = credit analyst |
| stage_offer | bool | |
| stage_accepted | bool | |
| stage_drawdown | bool | |
| repeat_90d | bool | repeat/top-up within 90 days (delayed outcome) |

## data/truth.json
```json
{
  "prequal_effect_data_complete_pp": 6.0,
  "prequal_effect_drawdown_pp": 2.0,
  "topup_effect_repeat_90d_pp": 4.0,
  "shock_week": 14, "shock_effect_pp": -5.0,
  "solo_launch_partner": 7, "solo_launch_week": 8,
  "prequal_start_day": {"0": 28, "1": 35, "...": "..."},
  "partner_base_rate": {"0": 0.31, "...": "..."}
}
```

## API (FastAPI, prefix /v1)
- `GET /v1/funnel/{partner_id}` → `{partner_id, n, stages: {data_complete, offer, accepted, drawdown, repeat_90d}: {count, rate_from_previous, rate_from_start}, routed_auto_share}`
- `POST /v1/experiments/power` body `{baseline_rate, mde_pp, alpha=0.05, power=0.8, daily_traffic}` → `{n_per_arm, days_to_run}`
- `POST /v1/experiments/analyse` body `{control: {n, conversions}, treatment: {n, conversions}, planned_n_per_arm?}` → `{lift_pp, ci95: [lo, hi], p_value, prob_treatment_better, ready_to_read: bool, note}`
- `GET /v1/partners/{partner_id}/posterior` → `{raw_rate, n, posterior_mean, ci94: [lo, hi], shrinkage}` (moonshot)
- `GET /health` → `{status, version}`; `GET /metrics` → `{requests, p50_ms, p95_ms, error_rate, last_error}` (moonshot)
- Errors: 404 unknown partner, 422 validation, all with `{detail}`.

### GOAL validation and denominator details

- Contracts live in `api/models.py`; unknown request fields are rejected.
- Funnel rates are fractions in [0, 1]. The first stage's previous denominator
  is `n`; later stages use the preceding stage count. A zero denominator gives
  rate 0. `routed_auto_share` is automatic routes / data-complete journeys,
  or 0 when none are complete. Repeat outcomes have complete 90-day follow-up.
- Power requires finite values, `0 < baseline_rate < baseline_rate + mde_pp/100 < 1`,
  `0 < alpha < power < 1`, and positive total eligible `daily_traffic` across
  both arms. `days_to_run` is recruitment time, excluding outcome follow-up.
- Arm counts are strict integers: `n > 0`, `0 <= conversions <= n`. Optional
  `planned_n_per_arm` is null or a positive strict integer. `ci95` has two values
  in percentage points. Readiness means both arms meet the supplied sample size;
  it is false without a plan and does not establish outcome maturity.
- GOAL response models are `Funnel` (nested `Stages` / `Stage`), `PowerResult`,
  `AnalyseResult`, and `Health`. Health status is `ok`; version is a string.
  `Error.detail` is a string for 404 or a list of validation errors for 422.
