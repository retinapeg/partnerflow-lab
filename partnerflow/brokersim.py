"""Five fictional brokers sending about a million API requests: simulated at scale.

This is separate from the three API partners of the live lab. It is a larger,
purely simulated estate for practising monitoring at volume. Nothing here
touches the API or its database.

The trick that makes a million requests cheap: nothing is stored per request.
Each broker-hour is one row holding counts (requests, failures by type) and
latency summaries. Latencies are drawn per request in one vectorised pass,
summarised per hour, then thrown away. 5 brokers x 14 days x 24 hours = 1,680
rows stand in for ~1,000,000 requests.

Two smaller tables are generated per item, because the questions asked of
them need individuals: applications (~50,000) and support tickets (~2,000).

Everything is decided by the seed. Same scenario, same numbers.
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

ERROR_TYPES = ["authentication", "timeout", "validation", "webhook", "other"]

# share: fraction of all traffic.  failure_rate: long-run chance a request fails.
# completion: chance an application is completed when NO API error occurs.
# Note the deliberate confounder: brokers with worse failure rates also have
# lower completion for unrelated reasons (customer mix, loan size), so a raw
# comparison across brokers overstates what errors cause.
BROKERS = {
    "northgate": {
        "name": "Northgate Finance", "share": 0.40, "failure_rate": 0.015,
        "latency_median_ms": 170, "latency_sigma": 0.35,
        "error_mix": [0.15, 0.35, 0.30, 0.10, 0.10],
        "apps_per_1000_requests": 50, "completion": 0.74, "approval": 0.62, "loan_median": 15_000,
    },
    "harbourline": {
        "name": "Harbourline Brokers", "share": 0.25, "failure_rate": 0.025,
        "latency_median_ms": 190, "latency_sigma": 0.40,
        "error_mix": [0.10, 0.30, 0.40, 0.10, 0.10],
        "apps_per_1000_requests": 50, "completion": 0.68, "approval": 0.60, "loan_median": 20_000,
    },
    "meridian": {
        "name": "Meridian Capital", "share": 0.18, "failure_rate": 0.040,
        "latency_median_ms": 220, "latency_sigma": 0.45,
        "error_mix": [0.10, 0.45, 0.25, 0.10, 0.10],
        "apps_per_1000_requests": 50, "completion": 0.62, "approval": 0.58, "loan_median": 28_000,
    },
    "ashgrove": {
        "name": "Ashgrove Lending", "share": 0.12, "failure_rate": 0.020,
        "latency_median_ms": 160, "latency_sigma": 0.30,
        "error_mix": [0.30, 0.25, 0.25, 0.10, 0.10],
        "apps_per_1000_requests": 50, "completion": 0.70, "approval": 0.61, "loan_median": 12_000,
    },
    "quill": {
        "name": "Quill & Co", "share": 0.05, "failure_rate": 0.060,
        "latency_median_ms": 260, "latency_sigma": 0.50,
        "error_mix": [0.10, 0.25, 0.45, 0.10, 0.10],
        "apps_per_1000_requests": 50, "completion": 0.50, "approval": 0.55, "loan_median": 45_000,
    },
}
BROKER_IDS = list(BROKERS)

# Traffic by hour of day (business hours heavy) and by day of week (Mon..Sun).
HOUR_PROFILE = np.array([0.25, 0.20, 0.15, 0.15, 0.20, 0.30, 0.50, 0.90, 1.40, 1.70, 1.80, 1.80,
                         1.60, 1.70, 1.80, 1.70, 1.50, 1.20, 0.90, 0.70, 0.60, 0.50, 0.40, 0.30])
DOW_FACTOR = np.array([1.00, 1.05, 1.05, 1.00, 0.95, 0.55, 0.45])
START = pd.Timestamp("2026-09-28 00:00")   # a Monday; a fictional fortnight

# --- Ground truth of the simulation -----------------------------------------
# An application makes several API calls. If any fails, the customer is this
# much less likely to complete the application (absolute drop in probability).
TRUE_ERROR_EFFECT = 0.35
MEAN_EXTRA_API_CALLS = 4          # calls per application = 1 + Poisson(4)
FUNDING_RATE = 0.85

# Support tickets: background rate per 1,000 requests, and the chance that a
# failure of each type produces a ticket.
BACKGROUND_TICKETS_PER_1000 = 0.3
BACKGROUND_MIX = [0.10, 0.05, 0.20, 0.10, 0.55]          # same order as ERROR_TYPES
TICKET_PROPENSITY = {"authentication": 0.10, "timeout": 0.06, "validation": 0.04,
                     "webhook": 0.15, "other": 0.03}


@dataclass(frozen=True)
class Incident:
    """A period during which one broker's requests fail more (and may slow down)."""

    broker: str
    start_hour: int          # hour index from the start of the period
    duration_hours: int
    failure_rate: float      # the failure rate during the incident (absolute)
    error_type: str = "timeout"
    latency_multiplier: float = 1.0
    label: str = "incident"

    @property
    def end_hour(self) -> int:
        return self.start_hour + self.duration_hours - 1


# The built-in incident: Meridian's timeouts spike from 09:00 to 16:59 on day 10.
BUILT_IN_INCIDENT = Incident(broker="meridian", start_hour=9 * 24 + 9, duration_hours=8,
                             failure_rate=0.14, error_type="timeout",
                             latency_multiplier=2.5, label="built-in incident")


@dataclass(frozen=True)
class Scenario:
    seed: int = 11
    days: int = 14
    total_requests: int = 1_000_000
    incidents: tuple[Incident, ...] = (BUILT_IN_INCIDENT,)

    @property
    def hours(self) -> int:
        return self.days * 24


# =============================================================================
# Hourly aggregates
# =============================================================================

def expected_requests(scenario: Scenario) -> pd.DataFrame:
    """One row per broker-hour with the expected number of requests."""
    hours = np.arange(scenario.hours)
    timestamps = START + pd.to_timedelta(hours, unit="h")
    weight = HOUR_PROFILE[timestamps.hour] * DOW_FACTOR[timestamps.dayofweek]
    weight = weight / weight.sum()
    rows = []
    for broker, spec in BROKERS.items():
        rows.append(pd.DataFrame({
            "broker": broker, "hour_index": hours, "timestamp": timestamps,
            "expected_requests": scenario.total_requests * spec["share"] * weight,
        }))
    return pd.concat(rows, ignore_index=True)


def _apply_incidents(frame: pd.DataFrame, scenario: Scenario) -> pd.DataFrame:
    """Add the true failure rate, latency multiplier and incident label per cell."""
    frame["true_failure_rate"] = frame["broker"].map({b: s["failure_rate"] for b, s in BROKERS.items()})
    frame["latency_multiplier"] = 1.0
    frame["incident"] = ""
    frame["incident_error_type"] = ""
    for incident in scenario.incidents:
        cells = ((frame["broker"] == incident.broker)
                 & (frame["hour_index"] >= incident.start_hour)
                 & (frame["hour_index"] <= incident.end_hour))
        frame.loc[cells, "true_failure_rate"] = incident.failure_rate
        frame.loc[cells, "latency_multiplier"] = incident.latency_multiplier
        frame.loc[cells, "incident"] = incident.label
        frame.loc[cells, "incident_error_type"] = incident.error_type
    return frame


def _error_type_probabilities(frame: pd.DataFrame) -> np.ndarray:
    """Per cell, the share of failures of each type.

    Outside an incident this is the broker's usual mix. During one, the extra
    failures are all of the incident's type, so the mix shifts towards it.
    """
    base_rate = frame["broker"].map({b: s["failure_rate"] for b, s in BROKERS.items()}).to_numpy()
    base_mix = np.vstack([BROKERS[b]["error_mix"] for b in frame["broker"]])
    extra = np.clip(frame["true_failure_rate"].to_numpy() - base_rate, 0, None)
    one_hot = np.zeros_like(base_mix)
    for i, error_type in enumerate(ERROR_TYPES):
        one_hot[(frame["incident_error_type"] == error_type).to_numpy(), i] = 1.0
    weighted = base_mix * base_rate[:, None] + one_hot * extra[:, None]
    return weighted / weighted.sum(axis=1, keepdims=True)


def _latency_summaries(frame: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Draw every request's latency once, summarise per cell, discard the draws."""
    counts = frame["requests"].to_numpy()
    median = (frame["broker"].map({b: s["latency_median_ms"] for b, s in BROKERS.items()}).to_numpy()
              * frame["latency_multiplier"].to_numpy())
    sigma = frame["broker"].map({b: s["latency_sigma"] for b, s in BROKERS.items()}).to_numpy()
    # Lognormal: ln(latency) is normal with mean ln(median) and sd sigma.
    draws = rng.lognormal(np.repeat(np.log(median), counts), np.repeat(sigma, counts))

    p50 = np.full(len(frame), np.nan)
    p95 = np.full(len(frame), np.nan)
    mean = np.full(len(frame), np.nan)
    edges = np.concatenate([[0], np.cumsum(counts)])
    for i in range(len(frame)):
        cell = draws[edges[i]:edges[i + 1]]
        if len(cell):
            p50[i], p95[i] = np.percentile(cell, [50, 95])
            mean[i] = cell.mean()
    return pd.DataFrame({"latency_p50_ms": np.round(p50, 1), "latency_p95_ms": np.round(p95, 1),
                         "latency_mean_ms": np.round(mean, 1)}, index=frame.index)


def simulate_hourly(scenario: Scenario, rng: np.random.Generator) -> pd.DataFrame:
    frame = _apply_incidents(expected_requests(scenario), scenario)
    frame["requests"] = rng.poisson(frame["expected_requests"])
    frame["failures"] = rng.binomial(frame["requests"], frame["true_failure_rate"])
    by_type = rng.multinomial(frame["failures"].to_numpy(), _error_type_probabilities(frame))
    for i, error_type in enumerate(ERROR_TYPES):
        frame[f"failures_{error_type}"] = by_type[:, i]
    frame = pd.concat([frame, _latency_summaries(frame, rng)], axis=1)
    frame["observed_rate"] = frame["failures"] / frame["requests"].replace(0, np.nan)
    return frame.drop(columns=["expected_requests"])


# =============================================================================
# Applications (one row each)
# =============================================================================

def simulate_applications(hourly: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    apps_rate = hourly["broker"].map({b: s["apps_per_1000_requests"] for b, s in BROKERS.items()})
    started = rng.poisson(hourly["requests"] * apps_rate / 1000)
    cell = np.repeat(hourly.index.to_numpy(), started)
    n = len(cell)

    broker = hourly.loc[cell, "broker"].to_numpy()
    p_cell = hourly.loc[cell, "true_failure_rate"].to_numpy()
    completion = pd.Series(broker).map({b: s["completion"] for b, s in BROKERS.items()}).to_numpy()
    approval = pd.Series(broker).map({b: s["approval"] for b, s in BROKERS.items()}).to_numpy()
    loan_median = pd.Series(broker).map({b: s["loan_median"] for b, s in BROKERS.items()}).to_numpy()

    # An application makes k calls; the chance at least one fails is 1 - (1-p)^k.
    api_calls = 1 + rng.poisson(MEAN_EXTRA_API_CALLS, size=n)
    exposure = 1 - (1 - p_cell) ** api_calls
    had_api_error = rng.random(n) < exposure

    # Both possible outcomes are drawn from the same random number, so the
    # simulator knows what each customer would have done without the error.
    u = rng.random(n)
    would_complete_without_error = u < completion
    would_complete_with_error = u < completion - TRUE_ERROR_EFFECT
    completed = np.where(had_api_error, would_complete_with_error, would_complete_without_error)
    approved = completed & (rng.random(n) < approval)
    funded = approved & (rng.random(n) < FUNDING_RATE)
    stage = np.select([funded, approved, completed], ["funded", "approved", "submitted"], "started")

    loan_amount = np.clip(np.round(rng.lognormal(np.log(loan_median), 0.6) / 500) * 500, 1_000, 250_000)
    minutes = rng.integers(0, 60, size=n)
    numbers = np.arange(1, n + 1)
    return pd.DataFrame({
        "application_id": [f"A-{i:06d}" for i in numbers],
        "customer_id": [f"C-{i:06d}" for i in numbers],
        "broker": broker,
        "hour_index": hourly.loc[cell, "hour_index"].to_numpy(),
        "timestamp": hourly.loc[cell, "timestamp"].to_numpy() + pd.to_timedelta(minutes, unit="m"),
        "loan_amount": loan_amount.astype(int),
        "api_calls": api_calls,
        "had_api_error": had_api_error,
        "completed": completed,
        "approved": approved,
        "funded": funded,
        "stage_reached": stage,
        "would_complete_without_error": would_complete_without_error,   # ground truth
    })


# =============================================================================
# Support tickets (one row each) and a keyword classifier
# =============================================================================

TEMPLATES = {
    "authentication": [
        "All our calls to {broker} integration are returning 401 since this morning.",
        "API key rejected as unauthorised, we have not changed anything on our side.",
        "Getting 403 forbidden on the customers endpoint for every request.",
        "Our token stopped working at {hour}:00, is the credential revoked?",
        "Signature check fails on POST /applications, please confirm which key is live.",
        "We cannot log any calls in, every attempt is refused at the door.",          # no keyword
    ],
    "timeout": [
        "Requests to /applications are timing out after 30 seconds.",
        "Seeing 504 gateway timeout on roughly one call in ten.",
        "The decision call has become very slow, latency over 20 seconds.",
        "Our integration hangs waiting for a response from the applications endpoint.",
        "No response from the API for several minutes around {hour}:00 today.",
        "Customers see the application screen spin forever after they press submit.",  # no keyword
    ],
    "validation": [
        "POST /applications returns 422 with field amount flagged.",
        "Payload rejected as invalid even though it matches your schema.",
        "We get 400 bad request for the trading_months field on some customers.",
        "Validation error on term_months, the docs say 60 is allowed.",
        "Every application for one customer segment is rejected with missing field.",
        "The webhook payload we send back has an invalid field according to your API.",  # ambiguous
    ],
    "webhook": [
        "We are not receiving webhook callbacks for decisions made after {hour}:00.",
        "Decision notification never arrived for several applications today.",
        "Our callback endpoint shows no deliveries from {broker} since this morning.",
        "Webhook deliveries are retrying three times and then giving up.",
        "The push of decision events to us stopped, applications look stuck.",
        "Customers were approved but nobody told our system.",                            # no keyword
    ],
    "other": [
        "Please update the contact email on our broker account.",
        "Can we get a sandbox key for a new developer joining next week?",
        "What is the maximum loan amount for invoice finance?",
        "Our monthly statement shows a different total to our records.",
        "Request to add a second contact to the integration mailing list.",
        "Question about the field format for annual turnover in the docs.",                # 'field' keyword
    ],
}

KEYWORDS = {
    "authentication": ["401", "403", "unauthori", "api key", "credential", "token", "forbidden",
                       "revoked", "signature", "refused"],
    "timeout": ["timeout", "timed out", "timing out", "504", "slow", "latency", "hang",
                "no response", "unresponsive", "gateway"],
    "validation": ["422", "400", "invalid", "validation", "rejected", "schema", "missing field",
                   "bad request", "field"],
    "webhook": ["webhook", "callback", "notification", "not receiving", "never arrived",
                "deliveries", "push of", "stuck"],
}


def classify_ticket(text: str) -> str:
    """Keyword rules: the category with the most keyword hits, or 'other' if none."""
    lowered = text.lower()
    scores = {category: sum(lowered.count(word) for word in words) for category, words in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else "other"


def simulate_tickets(hourly: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    background = rng.poisson(BACKGROUND_TICKETS_PER_1000 * hourly["requests"] / 1000)
    background_types = rng.multinomial(background, np.tile(BACKGROUND_MIX, (len(hourly), 1)))
    counts = {}
    for i, error_type in enumerate(ERROR_TYPES):
        failure_driven = rng.poisson(hourly[f"failures_{error_type}"] * TICKET_PROPENSITY[error_type])
        counts[error_type] = background_types[:, i] + failure_driven

    rows = []
    for error_type, count in counts.items():
        cell = np.repeat(hourly.index.to_numpy(), count)
        rows.append(pd.DataFrame({
            "broker": hourly.loc[cell, "broker"].to_numpy(),
            "hour_index": hourly.loc[cell, "hour_index"].to_numpy(),
            "timestamp": hourly.loc[cell, "timestamp"].to_numpy(),
            "true_category": error_type,
        }))
    tickets = pd.concat(rows, ignore_index=True).sort_values(["hour_index", "broker"]).reset_index(drop=True)
    tickets["timestamp"] = tickets["timestamp"] + pd.to_timedelta(rng.integers(0, 60, len(tickets)), unit="m")

    texts = []
    for broker, timestamp, category in zip(tickets["broker"], tickets["timestamp"], tickets["true_category"]):
        template = TEMPLATES[category][rng.integers(len(TEMPLATES[category]))]
        texts.append(template.format(broker=BROKERS[broker]["name"], hour=f"{timestamp.hour:02d}"))
    tickets["text"] = texts
    tickets["predicted_category"] = [classify_ticket(text) for text in texts]
    tickets.insert(0, "ticket_id", [f"T-{i:05d}" for i in range(1, len(tickets) + 1)])
    return tickets


# =============================================================================
# Everything together
# =============================================================================

def simulate(scenario: Scenario = Scenario()) -> dict:
    """Run the whole simulation. Returns hourly, applications, tickets and the truth."""
    rng = np.random.default_rng(scenario.seed)
    hourly = simulate_hourly(scenario, rng)
    applications = simulate_applications(hourly, rng)
    tickets = simulate_tickets(hourly, rng)
    return {
        "scenario": scenario,
        "hourly": hourly,
        "applications": applications,
        "tickets": tickets,
        "truth": {
            "failure_rates": {b: s["failure_rate"] for b, s in BROKERS.items()},
            "completion": {b: s["completion"] for b, s in BROKERS.items()},
            "error_effect": TRUE_ERROR_EFFECT,
            "incidents": scenario.incidents,
        },
    }
