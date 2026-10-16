"""Broker monitoring page: a million simulated requests from five brokers.

This page is separate from the live API and its three partners. It is a
larger, purely simulated estate for practising statistical monitoring at
volume. Everything on it is computed from the simulation each time the
scenario changes; nothing is hardcoded.
"""

import time

import pandas as pd
import streamlit as st

from partnerflow import brokersim, charts, monitoring
from partnerflow.brokersim import BROKERS, BUILT_IN_INCIDENT, ERROR_TYPES, Incident, Scenario
from views import common

NAMES = {broker: spec["name"] for broker, spec in BROKERS.items()}


@st.cache_data(show_spinner="Simulating a fortnight of broker traffic...")
def run_simulation(seed: int, days: int, total_requests: int, incidents: tuple) -> dict:
    scenario = Scenario(seed=seed, days=days, total_requests=total_requests,
                        incidents=tuple(Incident(*fields) for fields in incidents))
    started = time.perf_counter()
    result = brokersim.simulate(scenario)
    result["seconds"] = time.perf_counter() - started
    return result


def render(lab: dict) -> None:
    st.title("Broker monitoring")
    st.write(
        "Five fictional brokers send about a million requests over a fortnight. The page "
        "answers the monitoring questions: what is each broker's normal failure rate, which "
        "hours are unusual beyond chance, what an incident looks like in the evidence, and "
        "what it cost in applications. This is a separate simulated estate from the three "
        "API partners on the other pages, scaled up so the statistics have something to bite on."
    )

    scenario, baseline_hours = controls()
    sim = run_simulation(scenario.seed, scenario.days, scenario.total_requests,
                         tuple((i.broker, i.start_hour, i.duration_hours, i.failure_rate,
                                i.error_type, i.latency_multiplier, i.label) for i in scenario.incidents))
    hourly, applications, tickets = sim["hourly"], sim["applications"], sim["tickets"]
    chart = monitoring.control_chart(hourly, baseline_hours)
    incidents = monitoring.group_incidents(chart, baseline_hours)

    volume(sim, chart)
    comparison(chart, baseline_hours)
    control_charts(chart, baseline_hours, incidents)
    selected = detected_incidents(incidents, chart)
    if selected is not None:
        investigate(selected, chart, applications, tickets, baseline_hours)
        lending(selected, applications, hourly, baseline_hours)
    support_tickets(tickets, selected)
    the_maths()


# =============================================================================
# Scenario controls
# =============================================================================

def controls() -> tuple[Scenario, int]:
    with st.container(border=True):
        st.write("**Scenario**")
        cols = st.columns(4)
        seed = cols[0].number_input("Seed", 0, 9999, value=11, help="Same seed, same data.")
        days = cols[1].slider("Days simulated", 7, 28, value=14)
        baseline_days = cols[2].slider("Baseline days (define 'normal')", 2, max(2, days - 1),
                                       value=min(7, days - 1),
                                       help="Expected rates are estimated from these first days.")
        built_in = cols[3].toggle("Built-in incident", value=True,
                                  help="Meridian's timeouts spike for 8 hours on day 10.")

        faults = st.session_state.setdefault("broker_faults", [])
        with st.expander("Introduce a fault of your own"):
            with st.form("new_fault", clear_on_submit=False):
                cols = st.columns(4)
                broker = cols[0].selectbox("Broker", list(BROKERS), format_func=NAMES.get)
                day = cols[1].number_input("Day (1 = first day)", 1, days, value=min(12, days))
                hour = cols[2].number_input("Start hour (0-23)", 0, 23, value=10)
                duration = cols[3].number_input("Duration (hours)", 1, 72, value=3)
                cols = st.columns(4)
                rate = cols[0].slider("Failure rate during the fault", 0.0, 0.6, value=0.20, step=0.01)
                error_type = cols[1].selectbox("Error type", ERROR_TYPES, index=0)
                latency = cols[2].slider("Latency multiplier", 1.0, 5.0, value=1.0, step=0.5)
                cols[3].write("")
                if cols[3].form_submit_button("Add fault", type="primary"):
                    faults.append(Incident(broker, int((day - 1) * 24 + hour), int(duration), float(rate),
                                           error_type, float(latency), f"fault {len(faults) + 1}"))
            if faults:
                st.write("Active faults you added:")
                for fault in faults:
                    st.write(f"- **{fault.label}**: {NAMES[fault.broker]}, from "
                             f"{(brokersim.START + pd.Timedelta(hours=fault.start_hour)):%a %d %b %H:%M} "
                             f"for {fault.duration_hours} h, failure rate {fault.failure_rate:.0%}, "
                             f"{fault.error_type}, latency x{fault.latency_multiplier:g}")
                if st.button("Remove my faults"):
                    st.session_state["broker_faults"] = []
                    st.rerun()

    incidents = tuple(([BUILT_IN_INCIDENT] if built_in else []) + list(faults))
    baseline_hours = baseline_days * 24
    overlapping = [i for i in incidents if i.start_hour < baseline_hours]
    if overlapping:
        st.warning(
            f"{', '.join(i.label for i in overlapping)} overlaps the baseline period. The "
            "'expected' rate for that broker now includes the fault, which makes it harder to "
            "detect. Real monitoring has the same weakness: a baseline is only as clean as the "
            "history it was taken from.")
    return Scenario(seed=int(seed), days=int(days), incidents=incidents), baseline_hours


# =============================================================================
# Sections
# =============================================================================

def volume(sim: dict, chart: pd.DataFrame) -> None:
    cols = st.columns(5)
    common.tile(cols[0], "Requests represented", f"{int(chart['requests'].sum()):,}")
    common.tile(cols[1], "Rows stored", f"{len(chart):,}", "one per broker-hour")
    common.tile(cols[2], "Failures", f"{int(chart['failures'].sum()):,}")
    common.tile(cols[3], "Applications", f"{len(sim['applications']):,}", "one row each")
    common.tile(cols[4], "Generated in", f"{sim['seconds']:.2f} s", "whole simulation")
    st.caption("A million requests are never stored. Each broker-hour keeps counts and latency "
               "summaries; latencies are drawn per request in one pass, summarised, and discarded. "
               "That is enough for every question on this page.")


def comparison(chart: pd.DataFrame, baseline_hours: int) -> None:
    st.header("1. How do the brokers compare?")
    summary = monitoring.broker_summary(chart, baseline_hours)
    st.write("Observed failure rate over the whole period, with a 95% interval. The expected rate "
             "is estimated from the baseline days only; the design rate is what the simulator "
             "was told to use, which real monitoring never gets to see.")
    left, right = st.columns([2, 3])
    with left:
        st.altair_chart(charts.intervals(summary, "name", "observed_rate", "ci_low", "ci_high",
                                         value_title="failure rate (95% interval)"), width="stretch")
    with right:
        common.show_table(
            summary[["name", "requests", "failures", "observed_rate", "expected_rate", "design_rate",
                     "sd", "dispersion", "latency_p50_ms", "latency_p95_ms", "flagged_hours"]]
            .rename(columns={"name": "broker", "sd": "sd of rate"})
            .round({"dispersion": 2, "latency_p50_ms": 0, "latency_p95_ms": 0}),
            percent_columns=("observed_rate", "expected_rate", "design_rate", "sd of rate"))
    biggest, smallest = summary.iloc[0], summary.iloc[-1]
    st.write(
        f"**Reading it.** {biggest['name']} has {biggest['requests']:,} requests, so its rate is "
        f"known to within about {biggest['sd'] * 100:.2f} points; {smallest['name']} has "
        f"{smallest['requests']:,}, so its interval is "
        f"{(smallest['ci_high'] - smallest['ci_low']) / (biggest['ci_high'] - biggest['ci_low']):.0f}x "
        "wider. The standard deviation of a rate shrinks with the square root of the number of "
        "requests: four times the traffic halves the uncertainty."
    )
    st.write(
        "**Dispersion** compares how much each broker's hourly rate actually wobbles with how much "
        "binomial sampling alone says it should. About 1 means the process is stable and the "
        "hour-to-hour variation is pure chance. Well above 1 means something beyond chance is "
        "moving the rate: that is the first sign of an incident, before you even look for one."
    )


def control_charts(chart: pd.DataFrame, baseline_hours: int, incidents: pd.DataFrame) -> None:
    st.header("2. Which hours are unusual?")
    st.write(
        "A control chart per broker. Blue is the observed hourly failure rate, grey is the "
        "expected rate from the baseline days, and the band is expected +/- 3 standard "
        "deviations. The band is wider at night because fewer requests mean a noisier rate. "
        "A red dot is an hour whose failure count is too unlikely to be chance."
    )
    default_broker = incidents["broker"].iloc[0] if not incidents.empty else "northgate"
    cols = st.columns([2, 1], vertical_alignment="bottom")
    chosen = cols[0].selectbox("Broker", list(BROKERS), index=list(BROKERS).index(default_broker),
                               format_func=NAMES.get)
    show_all = cols[1].toggle("Show all five", value=False)
    for broker in (list(BROKERS) if show_all else [chosen]):
        rows = chart[chart["broker"] == broker]
        st.write(f"**{NAMES[broker]}**: {int(rows['flagged'].sum())} flagged hours")
        st.altair_chart(charts.control_chart(rows, height=120 if show_all else 200), width="stretch")

    n_tests = int((chart["requests"] > 0).sum())
    flagged = int(chart["flagged"].sum())
    st.write(
        f"**How many flags should chance produce?** {n_tests:,} broker-hours were tested. At the "
        f"3-sigma threshold (one-sided tail probability {monitoring.ALPHA_3_SIGMA:.5f}), about "
        f"**{monitoring.expected_false_alarms(n_tests):.1f}** flags are expected even when nothing "
        f"is wrong. **{flagged}** hours are flagged. An isolated flag is probably noise; a run of "
        "consecutive flagged hours is not, because the chance of k false flags in a row is the "
        "tiny tail probability raised to the power k."
    )
    st.caption("The flag uses the exact binomial tail probability rather than the z-score. For "
               "an hour with 30 requests, 3 failures against an expected 2% gives z = 3.1 but a "
               "tail probability of 2%: the normal approximation over-flags small counts.")


def detected_incidents(incidents: pd.DataFrame, chart: pd.DataFrame) -> pd.Series | None:
    st.header("3. Detected incidents")
    if incidents.empty:
        st.success("No hour is flagged. Add a fault above to see what detection looks like.")
        return None
    table = incidents.assign(
        when=incidents["start"].dt.strftime("%a %d %b %H:%M") + " to " + incidents["end"].dt.strftime("%H:%M"),
        broker=incidents["broker"].map(NAMES),
        dominant=incidents["dominant_error_type"] + " (" + (incidents["dominant_share"] * 100).round(0).astype(int).astype(str)
        + "% of failures, baseline " + (incidents["dominant_share_baseline"] * 100).round(0).astype(int).astype(str) + "%)",
        p95_change=(incidents["latency_p95_ms"] / incidents["latency_p95_baseline_ms"]),
    )
    common.show_table(
        common.with_p_text(table.rename(columns={"min_p_value": "p_value"}))[
            ["broker", "when", "hours", "requests", "failures", "observed_rate", "expected_rate",
             "excess_failures", "peak_z", "p_value", "dominant", "p95_change"]]
        .rename(columns={"dominant": "dominant error type", "p95_change": "p95 latency vs baseline"})
        .round({"peak_z": 1, "p95 latency vs baseline": 2}),
        percent_columns=("observed_rate", "expected_rate"))
    st.caption("Sorted by how unlikely the run is. Excess failures = failures beyond what the "
               "expected rate predicts for that many requests. A run of one hour with a modest z "
               "is probably one of the chance flags from the previous section.")
    labels = [f"{NAMES[row.broker]} · {row.start:%a %d %b %H:%M} · {row.hours} h" for row in incidents.itertuples()]
    choice = st.selectbox("Investigate", labels)
    return incidents.iloc[labels.index(choice)]


def investigate(incident: pd.Series, chart: pd.DataFrame, applications: pd.DataFrame,
                tickets: pd.DataFrame, baseline_hours: int) -> None:
    broker, start, end = incident["broker"], int(incident["start_hour"]), int(incident["end_hour"])
    st.header(f"4. Investigation: {NAMES[broker]}, {incident['start']:%a %d %b %H:%M} to {incident['end']:%H:%M}")

    cols = st.columns(5)
    common.tile(cols[0], "Observed failure rate", common.pct(incident["observed_rate"]),
                f"expected {common.pct(incident['expected_rate'])}")
    common.tile(cols[1], "Excess failures", f"{int(incident['excess_failures']):,}",
                f"of {int(incident['requests']):,} requests")
    common.tile(cols[2], "Peak z-score", f"{incident['peak_z']:.1f} σ")
    common.tile(cols[3], "Tail probability", "< 1e-6" if incident["min_p_value"] < 1e-6 else f"{incident['min_p_value']:.2g}")
    common.tile(cols[4], "p95 latency", f"{incident['latency_p95_ms']:.0f} ms",
                f"baseline {incident['latency_p95_baseline_ms']:.0f} ms")

    window = chart[chart["hour_index"].between(start, end)]
    left, right = st.columns(2)
    with left:
        st.write("**Is it only this broker?** Every broker in the same hours:")
        others = window.groupby("broker").agg(requests=("requests", "sum"), failures=("failures", "sum"),
                                              expected=("expected_rate", "first"), flagged_hours=("flagged", "sum"))
        others["observed"] = others["failures"] / others["requests"]
        others.index = others.index.map(NAMES)
        common.show_table(others.reset_index(names="broker")[["broker", "requests", "observed", "expected", "flagged_hours"]],
                          percent_columns=("observed", "expected"))
        st.caption("If every broker had moved, the cause would be on our side (a release, a "
                   "dependency). One broker moving points at that integration, or at a code "
                   "path only they use.")
    with right:
        st.write("**What kind of failures?** Share of each error type, baseline vs this window:")
        baseline = chart[(chart["broker"] == broker) & chart["in_baseline"]]
        mix = pd.DataFrame({
            "error type": ERROR_TYPES,
            "baseline": [baseline[f"failures_{t}"].sum() / max(1, baseline["failures"].sum()) for t in ERROR_TYPES],
            "during incident": [window[window["broker"] == broker][f"failures_{t}"].sum()
                                / max(1, window[window["broker"] == broker]["failures"].sum()) for t in ERROR_TYPES],
        })
        common.show_table(mix, percent_columns=("baseline", "during incident"))
        st.caption("The error mix is the fingerprint. Timeouts with higher latency say capacity "
                   "or a slow dependency; authentication says credentials; validation after a "
                   "release says a contract change.")

    affected = monitoring.applications_affected(applications, broker, start, end)
    in_window = applications[(applications["broker"] == broker) & applications["hour_index"].between(start, end)]
    st.write(f"**Which customers were affected?** {len(in_window):,} applications were in progress "
             f"for {NAMES[broker]} during the window; **{len(affected):,}** of them hit at least one "
             f"API error, and {int((~affected['stage_reached'].eq('started')).sum()):,} of those "
             "still completed. The rest stalled at 'started'.")
    common.show_table(affected.assign(timestamp=affected["timestamp"].dt.strftime("%d %b %H:%M")), height=220)

    ticket_window = tickets[(tickets["broker"] == broker) & tickets["hour_index"].between(start, end)]
    ticket_rate_before = len(tickets[(tickets["broker"] == broker) & (tickets["hour_index"] < baseline_hours)]) / baseline_hours
    st.write(f"**Support tickets in the window:** {len(ticket_window)} ({len(ticket_window) / max(1, incident['hours']):.1f} per hour, "
             f"against {ticket_rate_before:.1f} per hour in the baseline). By predicted category: "
             + ", ".join(f"{k} {v}" for k, v in ticket_window["predicted_category"].value_counts().items()))
    with st.expander("Read the tickets"):
        common.show_table(ticket_window[["ticket_id", "timestamp", "predicted_category", "text"]])

    st.write("**What an integration manager would write down:**")
    st.info(
        f"From {incident['start']:%H:%M} to {incident['end']:%H:%M} on {incident['start']:%a %d %b}, "
        f"{NAMES[broker]}'s failure rate was {common.pct(incident['observed_rate'])} against an expected "
        f"{common.pct(incident['expected_rate'])} ({int(incident['excess_failures']):,} excess failures, "
        f"peak {incident['peak_z']:.0f} sigma). Other brokers were normal. "
        f"{incident['dominant_error_type'].capitalize()} errors rose from "
        f"{common.pct(incident['dominant_share_baseline'], 0)} to {common.pct(incident['dominant_share'], 0)} of failures"
        + (f" and p95 latency went from {incident['latency_p95_baseline_ms']:.0f} ms to {incident['latency_p95_ms']:.0f} ms"
           if incident["latency_p95_ms"] > 1.3 * incident["latency_p95_baseline_ms"] else "")
        + f". {len(affected):,} customers hit an error; {len(ticket_window)} tickets were raised. "
        "Next: check what changed on that integration at the start time, confirm recovery on the "
        "chart, and contact the affected customers."
    )


def lending(incident: pd.Series, applications: pd.DataFrame, hourly: pd.DataFrame,
            baseline_hours: int) -> None:
    broker, start, end = incident["broker"], int(incident["start_hour"]), int(incident["end_hour"])
    st.header("5. What did it cost? Reliability and lending outcomes")
    evidence = monitoring.completion_evidence(applications, hourly, broker, start, end, baseline_hours)
    before, during = evidence["affected_before"], evidence["affected_during"]

    cols = st.columns(4)
    common.tile(cols[0], "Completion before", common.pct(before["rate"]), f"{before['n']:,} applications (baseline)")
    common.tile(cols[1], "Completion during", common.pct(during["rate"]), f"{during['n']:,} applications")
    common.tile(cols[2], "Share that hit an error", common.pct(evidence["exposure_during"]),
                f"baseline {common.pct(evidence['exposure_before'])}")
    common.tile(cols[3], "Estimated lost applications", f"{evidence['lost_applications']:.0f}",
                f"≈ £{evidence['lost_loan_value'] / 1e6:.1f}m requested")

    st.write("**Three ways to ask whether the failures caused the drop, weakest to strongest:**")
    estimates = pd.DataFrame([
        {"estimate": "Before vs during, this broker only", "effect": evidence["naive_change"],
         "low": evidence["naive_ci"][0], "high": evidence["naive_ci"][1]},
        {"estimate": "Difference-in-differences vs other brokers", "effect": evidence["did"],
         "low": evidence["did_ci"][0], "high": evidence["did_ci"][1]},
    ])
    st.altair_chart(charts.intervals(estimates, "estimate", "effect", "low", "high", value_format="+.1%",
                                     reference=evidence["truth"],
                                     value_title="change in completion rate (grey line = true effect in the simulator)"),
                    width="stretch")
    cross = evidence["cross_broker"].assign(broker=evidence["cross_broker"]["broker"].map(NAMES))
    left, right = st.columns([3, 2])
    with left:
        st.write(
            f"1. **Across brokers.** Failure rate and completion rate are correlated at "
            f"r = {evidence['cross_broker_r']:.2f} over the five brokers. Strong, and mostly "
            "misleading: the brokers with worse integrations also send larger loans and different "
            "customers, who complete less anyway. Five points cannot separate those."
        )
        st.write(
            f"2. **Before vs during, same broker.** Completion fell by "
            f"{common.points(evidence['naive_change'])} (95% interval "
            f"{common.points(evidence['naive_ci'][0])} to {common.points(evidence['naive_ci'][1])}). "
            "Better: the broker's customer mix did not change in eight hours. But anything else "
            "that changed at the same time is still mixed in."
        )
        st.write(
            f"3. **Difference-in-differences.** The other brokers' completion moved by "
            f"{common.points(evidence['others_during']['rate'] - evidence['others_before']['rate'])} over the "
            f"same hours. Subtracting that gives {common.points(evidence['did'])} (interval "
            f"{common.points(evidence['did_ci'][0])} to {common.points(evidence['did_ci'][1])}). "
            "This nets out anything that affected everyone at that time. It is the strongest "
            "evidence available without an experiment."
        )
        st.write(
            f"**The truth** built into the simulator for these customers is "
            f"{common.points(evidence['truth'])}. Estimate 3 brackets it. Within the broker, hour "
            f"by hour, the correlation is only r = {evidence['within_broker_r']:.2f}: with a few "
            "dozen applications an hour, single hours are too noisy to show it."
        )
    with right:
        st.write("Per broker, whole period:")
        common.show_table(cross[["broker", "failure_rate", "completion_rate", "applications"]],
                          percent_columns=("failure_rate", "completion_rate"))
    st.caption("Why a failure costs completions: an application makes several API calls. If any "
               "fails, the customer is less likely to finish. With k calls at failure rate p, the "
               "chance of at least one failure is 1 - (1 - p)^k, which is why a modest rise in p "
               "reaches a large share of applications.")


def support_tickets(tickets: pd.DataFrame, incident: pd.Series | None) -> None:
    st.header("6. Support tickets")
    report = monitoring.classifier_report(tickets)
    st.write(
        f"{len(tickets):,} tickets, each sorted into a category by keyword rules "
        f"(partnerflow/brokersim.py). Against the true causes the rules are "
        f"**{report['accuracy']:.0%}** accurate. The chart counts tickets per day by predicted category."
    )
    by_day = monitoring.tickets_by_day(tickets)
    st.bar_chart(by_day, x="day", y="tickets", color="predicted_category", stack=True, height=220)
    left, right = st.columns(2)
    with left:
        st.write("Precision and recall per category:")
        common.show_table(report["per_category"], percent_columns=("recall", "precision"))
    with right:
        st.write("Confusion matrix (rows: true cause, columns: predicted):")
        st.dataframe(report["matrix"])
    wrong = tickets[tickets["true_category"] != tickets["predicted_category"]]
    with st.expander(f"The {len(wrong)} misclassified tickets, and why"):
        st.write("Keyword rules miss tickets that describe a problem without naming it, and are "
                 "fooled by a keyword used in another sense. The fix is a trained classifier on "
                 "labelled tickets, or at least a richer keyword list; keep the labelled set to "
                 "measure either.")
        common.show_table(wrong[["ticket_id", "true_category", "predicted_category", "text"]].head(40))


def the_maths() -> None:
    st.header("7. The maths on this page")
    with st.expander("Binomial variance and the 1/sqrt(n) law"):
        st.latex(r"\hat p = \frac{x}{n}, \qquad \mathrm{Var}(\hat p) = \frac{p(1-p)}{n}, \qquad \mathrm{sd}(\hat p) = \sqrt{\frac{p(1-p)}{n}}")
        st.write("Each request fails independently with probability p, so the number of failures "
                 "in n requests is binomial. The observed rate is an average of n coin flips, and "
                 "the uncertainty of an average falls like one over the square root of n. This is "
                 "the same law that makes relative fluctuations in a system of N particles scale "
                 "as 1/sqrt(N): a broker with 400,000 requests has a sharply defined rate; one with "
                 "50,000 does not.")
    with st.expander("Control limits and z-scores"):
        st.latex(r"z = \frac{\hat p - p}{\sqrt{p(1-p)/n}}, \qquad \text{limits: } p \pm 3\sqrt{\frac{p(1-p)}{n}}")
        st.write("The 3-sigma rule: if the hourly rate were normally distributed around p, only "
                 "0.135% of hours would land above the upper limit by chance. The limits depend on "
                 "n, so they widen at night when traffic is thin.")
    with st.expander("Why the flag uses the exact binomial tail"):
        st.latex(r"P(X \ge x) = \sum_{j=x}^{n} \binom{n}{j} p^{j} (1-p)^{n-j}")
        st.write("The normal approximation needs np to be comfortably above 5. At night the small "
                 "broker has 30 requests and an expected 0.6 failures, so 3 failures looks like "
                 "3 sigma but has a 2% tail probability, not 0.1%. Using the exact tail keeps the "
                 "false-alarm rate honest everywhere on the chart.")
    with st.expander("Wilson interval for a rate"):
        st.latex(r"\frac{\hat p + \frac{z^2}{2n} \pm z\sqrt{\frac{\hat p(1-\hat p)}{n} + \frac{z^2}{4n^2}}}{1 + \frac{z^2}{n}}")
        st.write("Preferred over p +/- z*sd because it stays inside 0 to 1 and behaves well when "
                 "p is small, which failure rates always are.")
    with st.expander("Multiple comparisons"):
        st.latex(r"\mathbb{E}[\text{false flags}] = N_{\text{tests}} \times \alpha")
        st.write("Testing 1,680 broker-hours at alpha = 0.00135 produces about 2.3 false flags "
                 "even when nothing is wrong. Either accept and read flags in context (a run of "
                 "k consecutive flags has probability about alpha^k under the null), or tighten "
                 "alpha in proportion to the number of tests.")
    with st.expander("Dispersion: is the wobble just noise?"):
        st.latex(r"D = \frac{\mathrm{Var}_{\text{observed}}(\hat p_h)}{\overline{p(1-p)/n_h}}")
        st.write("The variance of the hourly rates divided by the variance binomial sampling "
                 "predicts. About 1: the process is in control. Well above 1: the true rate is "
                 "itself changing between hours, which is what an incident does.")
    with st.expander("Exposure of an application to failures"):
        st.latex(r"P(\text{at least one failure}) = 1 - (1-p)^k")
        st.write("An application makes k calls. Small p, several calls: the share of applications "
                 "touched by a failure is several times p. Here k is about 5, so a 4% failure rate "
                 "reaches 18% of applications and a 14% rate reaches over half.")
    with st.expander("Difference-in-differences"):
        st.latex(r"\widehat{\text{effect}} = (\bar y^{A}_{\text{during}} - \bar y^{A}_{\text{before}}) - (\bar y^{O}_{\text{during}} - \bar y^{O}_{\text{before}})")
        st.latex(r"\mathrm{se}^2 = \sum_{\text{4 groups}} \frac{\bar y (1-\bar y)}{n}")
        st.write("A is the affected broker, O the others. The second bracket removes whatever "
                 "moved everyone during those hours (time of day, a marketing campaign). The "
                 "remaining assumption is that, without the incident, A would have moved like O. "
                 "That cannot be tested from the data, which is why the simulator's truth is shown "
                 "beside it.")
