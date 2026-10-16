"""The five-broker simulator: volume, consistency, the built-in incident, tickets."""

import pandas as pd
import pytest

from partnerflow import brokersim
from partnerflow.brokersim import BUILT_IN_INCIDENT, ERROR_TYPES, Incident, Scenario

TYPE_COLUMNS = [f"failures_{t}" for t in ERROR_TYPES]


@pytest.fixture(scope="module")
def default():
    return brokersim.simulate(Scenario())


def test_same_seed_gives_the_same_data(default):
    again = brokersim.simulate(Scenario())
    pd.testing.assert_frame_equal(default["hourly"], again["hourly"])
    pd.testing.assert_frame_equal(default["applications"], again["applications"])
    pd.testing.assert_frame_equal(default["tickets"], again["tickets"])
    other = brokersim.simulate(Scenario(seed=12))
    assert not default["hourly"]["requests"].equals(other["hourly"]["requests"])


def test_a_million_requests_fit_in_a_few_rows(default):
    hourly = default["hourly"]
    assert len(hourly) == 5 * 14 * 24
    assert abs(hourly["requests"].sum() - 1_000_000) < 10_000     # Poisson noise is ~1,000
    shares = hourly.groupby("broker")["requests"].sum() / hourly["requests"].sum()
    for broker, spec in brokersim.BROKERS.items():
        assert shares[broker] == pytest.approx(spec["share"], abs=0.01)


def test_hourly_counts_are_internally_consistent(default):
    hourly = default["hourly"]
    assert (hourly["failures"] <= hourly["requests"]).all()
    assert (hourly[TYPE_COLUMNS].sum(axis=1) == hourly["failures"]).all()
    assert (hourly["requests"] > 0).all()
    assert (hourly["latency_p95_ms"] >= hourly["latency_p50_ms"]).all()
    assert (hourly["latency_p50_ms"] > 0).all()
    assert hourly["observed_rate"].between(0, 1).all()


def test_traffic_follows_the_daily_profile(default):
    hourly = default["hourly"]
    by_hour = hourly.groupby(hourly["timestamp"].dt.hour)["requests"].sum()
    assert by_hour.loc[11] > 4 * by_hour.loc[3]         # midday busier than 3 am
    by_day = hourly.groupby(hourly["timestamp"].dt.dayofweek)["requests"].sum()
    assert by_day.loc[6] < 0.6 * by_day.loc[1]           # Sunday quieter than Tuesday


def test_the_built_in_incident_is_in_the_data(default):
    hourly = default["hourly"]
    cells = hourly[hourly["incident"] == "built-in incident"]
    assert len(cells) == 8
    assert set(cells["broker"]) == {"meridian"}
    assert cells["hour_index"].tolist() == list(range(225, 233))
    assert (cells["true_failure_rate"] == 0.14).all()
    assert cells["failures"].sum() / cells["requests"].sum() > 0.10
    assert cells["failures_timeout"].sum() / cells["failures"].sum() > 0.7

    baseline = hourly[(hourly["broker"] == "meridian") & (hourly["incident"] == "")]
    assert cells["latency_p95_ms"].median() > 2 * baseline["latency_p95_ms"].median()
    assert baseline["failures"].sum() / baseline["requests"].sum() == pytest.approx(0.04, abs=0.005)


def test_a_user_fault_is_applied_to_the_right_cells():
    fault = Incident("quill", start_hour=300, duration_hours=4, failure_rate=0.30,
                     error_type="authentication", label="my fault")
    hourly = brokersim.simulate(Scenario(incidents=(BUILT_IN_INCIDENT, fault)))["hourly"]
    cells = hourly[hourly["incident"] == "my fault"]
    assert len(cells) == 4 and set(cells["broker"]) == {"quill"}
    assert cells["failures"].sum() / cells["requests"].sum() > 0.2
    assert cells["failures_authentication"].sum() / cells["failures"].sum() > 0.6
    assert len(hourly) == 5 * 14 * 24                      # nothing else changed shape


def test_applications_are_consistent(default):
    apps = default["applications"]
    assert 40_000 < len(apps) < 60_000
    assert apps["application_id"].is_unique
    assert not (apps["funded"] & ~apps["approved"]).any()
    assert not (apps["approved"] & ~apps["completed"]).any()
    assert (apps.loc[apps["funded"], "stage_reached"] == "funded").all()
    assert (apps.loc[~apps["completed"], "stage_reached"] == "started").all()
    assert apps["api_calls"].min() >= 1
    assert apps["loan_amount"].between(1_000, 250_000).all()


def test_errors_reach_more_applications_during_the_incident(default):
    apps = default["applications"]
    meridian = apps[apps["broker"] == "meridian"]
    during = meridian[meridian["hour_index"].between(225, 232)]
    before = meridian[meridian["hour_index"] < 168]
    assert during["had_api_error"].mean() > 2 * before["had_api_error"].mean()
    assert during["completed"].mean() < before["completed"].mean()
    # The exposure formula: chance at least one of k calls fails.
    k = before["api_calls"].mean()
    assert before["had_api_error"].mean() == pytest.approx(1 - (1 - 0.04) ** k, abs=0.03)


def test_keyword_classifier_on_clear_examples():
    assert brokersim.classify_ticket("Requests are timing out again") == "timeout"
    assert brokersim.classify_ticket("Every call returns 401 unauthorised") == "authentication"
    assert brokersim.classify_ticket("422 invalid payload on amount field") == "validation"
    assert brokersim.classify_ticket("We stopped receiving webhook callbacks") == "webhook"
    assert brokersim.classify_ticket("Please update our billing address") == "other"


def test_tickets_are_plausible_and_mostly_classified_correctly(default):
    tickets = default["tickets"]
    assert 1_000 < len(tickets) < 5_000
    assert tickets["ticket_id"].is_unique
    assert set(tickets["true_category"]) <= set(ERROR_TYPES)
    accuracy = (tickets["true_category"] == tickets["predicted_category"]).mean()
    assert 0.8 <= accuracy < 1.0        # deliberately imperfect: some templates lack keywords


def test_tickets_spike_in_the_incident_category(default):
    tickets = default["tickets"]
    meridian = tickets[tickets["broker"] == "meridian"]
    during = meridian[meridian["hour_index"].between(225, 232)]
    before = meridian[meridian["hour_index"] < 168]
    assert len(during) > 40
    assert (during["true_category"] == "timeout").mean() > 0.6
    assert (before["true_category"] == "timeout").mean() < 0.5
    assert len(during) / 8 > 3 * len(before) / 168          # tickets per hour jumped
