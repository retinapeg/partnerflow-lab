"""Reproducible synthetic dataset of fictional lending journeys.

This is a simulator, not real data. Its value is that the truth is known:
the code below states exactly how strongly an API error reduces completion and
how much the experiment's treatment helps. The analytics module then has to
recover those numbers from the data alone, and we can check whether it does.

Two effects are deliberately built in:

* A CONFOUNDER. Bigger applications both hit more API errors and are abandoned
  more often anyway (longer forms, more documents). So journeys with errors
  drop off more than the errors themselves cause.
* A RANDOMISED EXPERIMENT. Treatment is assigned by coin flip, independent of
  everything else, so a difference between the groups is causal.

Command line:
    python -m partnerflow.synthetic --seed 42 --n 12000 --out data/synthetic_journeys.csv
"""

import argparse

import numpy as np
import pandas as pd

PARTNERS = ["alder", "birch", "cedar"]
PARTNER_SHARE = [0.5, 0.3, 0.2]
PRODUCTS = ["term_loan", "credit_line", "invoice_finance"]
PRODUCT_SHARE = [0.5, 0.3, 0.2]

AMOUNT_BANDS = ["1k-10k", "10k-25k", "25k-50k", "50k-100k", "100k-250k"]
BAND_EDGES = [(1_000, 9_500), (10_000, 24_500), (25_000, 49_500), (50_000, 99_500), (100_000, 250_000)]

# Each partner's mix of amount bands. Cedar sends much larger applications.
BAND_MIX = {
    "alder": [0.40, 0.30, 0.18, 0.09, 0.03],
    "birch": [0.28, 0.30, 0.22, 0.14, 0.06],
    "cedar": [0.12, 0.22, 0.28, 0.24, 0.14],
}

# Chance of at least one API error during the journey. Rises with amount band
# (more documents, more calls) and differs by partner (integration quality).
ERROR_RATE_BY_BAND = [0.04, 0.07, 0.11, 0.16, 0.22]
ERROR_RATE_PARTNER_ADJ = {"alder": 0.00, "birch": 0.02, "cedar": 0.06}

# Chance of completing (submitting) the application when there is NO error.
# Falls with amount band. This is what makes amount band a confounder.
SUBMIT_RATE_BY_BAND = [0.78, 0.72, 0.64, 0.56, 0.48]
SUBMIT_RATE_PARTNER_ADJ = {"alder": 0.02, "birch": 0.00, "cedar": -0.03}

APPROVE_RATE_BY_BAND = [0.70, 0.66, 0.62, 0.56, 0.50]
OFFER_ACCEPT_RATE = 0.80
FUNDING_RATE = 0.92

# --- Ground truth of the simulation -----------------------------------------
TRUE_ERROR_EFFECT = -0.12   # an API error lowers the chance of submitting by 12 points
TRUE_AB_LIFT = 0.04         # the treatment raises the chance of submitting by 4 points

STAGES = ["started", "submitted", "approved", "offer_accepted", "funded"]


def generate_journeys(n: int = 12_000, seed: int = 42,
                      true_error_effect: float = TRUE_ERROR_EFFECT,
                      true_ab_lift: float = TRUE_AB_LIFT) -> pd.DataFrame:
    """Return one row per fictional customer journey. Same inputs, same rows."""
    rng = np.random.default_rng(seed)

    partner = rng.choice(PARTNERS, size=n, p=PARTNER_SHARE)
    product = rng.choice(PRODUCTS, size=n, p=PRODUCT_SHARE)

    # Amount band depends on the partner; the amount is uniform within the band.
    band_index = np.zeros(n, dtype=int)
    for name in PARTNERS:
        rows = partner == name
        band_index[rows] = rng.choice(len(AMOUNT_BANDS), size=rows.sum(), p=BAND_MIX[name])
    low = np.array([edge[0] for edge in BAND_EDGES])[band_index]
    high = np.array([edge[1] for edge in BAND_EDGES])[band_index]
    loan_amount = (rng.integers(low // 500, high // 500 + 1)) * 500

    partner_series = pd.Series(partner)
    error_adj = partner_series.map(ERROR_RATE_PARTNER_ADJ).to_numpy()
    submit_adj = partner_series.map(SUBMIT_RATE_PARTNER_ADJ).to_numpy()

    # API errors.
    p_error = np.array(ERROR_RATE_BY_BAND)[band_index] + error_adj
    had_api_error = rng.random(n) < p_error
    api_error_count = np.where(had_api_error, 1 + rng.poisson(0.6, size=n), 0)
    error_type = rng.choice(["timeout", "server_error", "validation_error"], size=n, p=[0.45, 0.25, 0.30])
    api_error_type = np.where(had_api_error, error_type, "none")

    # Latency: slower for bigger applications and when errors caused retries.
    median_latency = 180 + 40 * band_index + 60 * (partner == "cedar")
    latency_ms = median_latency * rng.lognormal(mean=0.0, sigma=0.35, size=n)
    latency_ms = np.round(latency_ms * np.where(had_api_error, 1.6, 1.0), 1)

    # The experiment: a fair coin flip, independent of everything above.
    treated = rng.random(n) < 0.5
    experiment_group = np.where(treated, "treatment", "control")

    # Would this journey submit? Work out both possibilities for every journey
    # (with and without an API error) from the same random number, then keep
    # the one that matches what happened. Real data only ever shows one.
    p_submit = np.array(SUBMIT_RATE_BY_BAND)[band_index] + submit_adj + true_ab_lift * treated
    u = rng.random(n)
    would_submit_without_error = u < p_submit
    would_submit_with_error = u < p_submit + true_error_effect
    submitted = np.where(had_api_error, would_submit_with_error, would_submit_without_error)

    # Later stages do not depend on errors or on the experiment.
    approved = submitted & (rng.random(n) < np.array(APPROVE_RATE_BY_BAND)[band_index])
    offer_accepted = approved & (rng.random(n) < OFFER_ACCEPT_RATE)
    funded = offer_accepted & (rng.random(n) < FUNDING_RATE)

    stage_index = submitted.astype(int) + approved + offer_accepted + funded
    stage_reached = np.array(STAGES)[stage_index]
    outcome = np.select(
        [funded, approved, submitted],
        ["funded", "abandoned_after_approval", "declined"],
        default="abandoned_before_submit",
    )

    numbers = range(1, n + 1)
    # Only journeys that were submitted have an application.
    application_id = [f"APP-{i:06d}" if done else "" for i, done in zip(numbers, submitted)]

    return pd.DataFrame({
        "journey_id": [f"J-{i:06d}" for i in numbers],
        "customer_id": [f"CUS-{i:06d}" for i in numbers],
        "application_id": application_id,
        "partner": partner,
        "product": product,
        "loan_amount": loan_amount,
        "amount_band": np.array(AMOUNT_BANDS)[band_index],
        "experiment_group": experiment_group,
        "had_api_error": had_api_error,
        "api_error_count": api_error_count,
        "api_error_type": api_error_type,
        "latency_ms": latency_ms,
        "stage_reached": stage_reached,
        "outcome": outcome,
        "submitted": submitted,
        "approved": approved,
        "offer_accepted": offer_accepted,
        "funded": funded,
        # Ground-truth columns. They exist only because this is a simulation.
        "would_submit_without_error": would_submit_without_error,
        "would_submit_with_error": would_submit_with_error,
    })


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Write the synthetic journeys dataset to CSV.")
    parser.add_argument("--n", type=int, default=12_000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", default="data/synthetic_journeys.csv")
    args = parser.parse_args()
    frame = generate_journeys(args.n, args.seed)
    frame.to_csv(args.out, index=False)
    print(f"Wrote {len(frame):,} journeys (seed {args.seed}) to {args.out}")
