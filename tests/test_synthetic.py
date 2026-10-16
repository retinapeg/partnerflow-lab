"""The synthetic journeys dataset: reproducible and internally consistent."""

import pandas as pd

from partnerflow import synthetic


def test_same_seed_gives_the_same_dataset():
    first = synthetic.generate_journeys(n=2_000, seed=42)
    second = synthetic.generate_journeys(n=2_000, seed=42)
    pd.testing.assert_frame_equal(first, second)


def test_different_seed_gives_different_data():
    first = synthetic.generate_journeys(n=2_000, seed=42)
    other = synthetic.generate_journeys(n=2_000, seed=43)
    assert not first["loan_amount"].equals(other["loan_amount"])


def test_dataset_has_the_required_columns():
    journeys = synthetic.generate_journeys(n=500, seed=1)
    required = {"partner", "customer_id", "application_id", "loan_amount", "stage_reached",
                "outcome", "api_error_count", "had_api_error", "latency_ms", "experiment_group"}
    assert required <= set(journeys.columns)
    assert len(journeys) == 500
    assert journeys["journey_id"].is_unique
    assert set(journeys["partner"]) == {"alder", "birch", "cedar"}
    assert set(journeys["experiment_group"]) == {"control", "treatment"}
    assert set(journeys["stage_reached"]) <= set(synthetic.STAGES)
    assert (journeys["latency_ms"] > 0).all()
    assert journeys["loan_amount"].between(1_000, 250_000).all()


def test_stages_are_consistent():
    journeys = synthetic.generate_journeys(n=5_000, seed=3)
    # Nobody skips a stage.
    assert not (journeys["funded"] & ~journeys["offer_accepted"]).any()
    assert not (journeys["offer_accepted"] & ~journeys["approved"]).any()
    assert not (journeys["approved"] & ~journeys["submitted"]).any()
    # Errors are counted only on journeys that had one.
    assert ((journeys["api_error_count"] > 0) == journeys["had_api_error"]).all()
    # Only submitted journeys have an application ID.
    assert ((journeys["application_id"] != "") == journeys["submitted"]).all()
    assert (journeys.loc[journeys["outcome"] == "funded", "stage_reached"] == "funded").all()


def test_experiment_assignment_is_balanced_and_unrelated_to_partner():
    journeys = synthetic.generate_journeys(n=12_000, seed=42)
    assert abs((journeys["experiment_group"] == "treatment").mean() - 0.5) < 0.02
    treated_share = journeys.groupby("partner")["experiment_group"].apply(
        lambda group: (group == "treatment").mean())
    assert ((treated_share - 0.5).abs() < 0.04).all()


def test_built_in_effects_can_be_changed():
    no_effect = synthetic.generate_journeys(n=4_000, seed=5, true_error_effect=0.0)
    assert no_effect["would_submit_with_error"].equals(no_effect["would_submit_without_error"])
