"""Overview page: is the API healthy, and what has happened so far?"""

import pandas as pd
import streamlit as st

from partnerflow import charts
from views import common

GUIDE = [
    ("API playground", "Send one request yourself and read exactly what came back."),
    ("Fault injection", "Switch on a failure, send traffic, and watch the API misbehave."),
    ("Release simulation", "The main story: a release breaks something, you find it and fix it."),
    ("Incident investigation", "Dig through recorded requests to find who failed and why."),
    ("Recurring issues", "Which failures keep coming back, the statistics that say so, and the "
                         "agent that triages them."),
    ("Data science", "Funnels, partner comparison, correlation vs causation, and an A/B test."),
    ("Broker monitoring", "Control charts and difference-in-differences at a million requests."),
]


def render(lab: dict) -> None:
    st.title("Overview")
    st.write(
        "PartnerFlow Lab is a pretend lending API with three pretend partners. "
        "You can send it requests, break it on purpose, and investigate what went wrong. "
        "Every number on this page is computed from requests the API really handled."
    )

    metrics = common.get_json("/metrics")
    requests, applications = metrics["requests"], metrics["applications"]

    start_here(requests["total"])

    st.subheader("API health")
    cols = st.columns(5)
    cols[0].metric("API status", "Up", border=True)
    cols[1].metric("Release", metrics["release"]["version"], border=True)
    cols[2].metric("Requests recorded", f"{requests['total']:,}", border=True)
    cols[3].metric("Error rate", common.pct(requests["error_rate"]), border=True,
                   help="Share of recorded requests that returned 400 or above.")
    cols[4].metric("p95 latency",
                   "-" if requests["latency_ms_p95"] is None else f"{requests['latency_ms_p95']:.0f} ms",
                   border=True, help="95% of requests were faster than this.")

    if requests["total"] == 0:
        st.info("Nothing has been recorded yet. Send some healthy traffic to fill the lab.")
        if st.button("Generate healthy traffic (150 customer journeys)", type="primary"):
            common.generate_traffic("healthy", 150, 7)
            st.rerun()
        show_guide()
        return

    left, right = st.columns(2)
    with left:
        st.write("**Responses by status code**")
        codes = pd.DataFrame({"status code": list(metrics["by_status_code"]),
                              "requests": list(metrics["by_status_code"].values())})
        st.altair_chart(charts.bars(codes, "status code", "requests", value_format=",d",
                                    value_title="requests"), width="stretch")
    with right:
        st.write("**Error rate by partner**")
        partners = pd.DataFrame(metrics["by_partner"]).dropna(subset=["error_rate"])
        if partners.empty:
            st.caption("No partner traffic yet.")
        else:
            st.altair_chart(charts.bars(partners, "partner_id", "error_rate",
                                        value_title="share of requests that failed"),
                            width="stretch")

    if metrics["by_failure_reason"]:
        st.write("**Why requests failed**")
        reasons = pd.DataFrame({"failure reason": list(metrics["by_failure_reason"]),
                                "requests": list(metrics["by_failure_reason"].values())})
        common.show_table(reasons)
        st.button("Investigate these failures", on_click=common.go_to,
                  args=("Incident investigation",))

    st.subheader("Customer conversion")
    log = common.load_log()
    sent = log[log["request"] == "POST /applications"]
    accepted = int((sent["status_code"] == 201).sum())
    st.write("From an application being sent to the API, to it being approved by the toy decision rule.")
    cols = st.columns(4)
    cols[0].metric("Applications sent", f"{len(sent):,}", border=True)
    common.tile(cols[1], "Accepted by the API", f"{accepted:,}",
                common.pct(accepted / len(sent)) + " of sent" if len(sent) else None)
    common.tile(cols[2], "Approved", f"{applications['approved']:,}",
                common.pct(applications["approved"] / len(sent)) + " of sent" if len(sent) else None)
    cols[3].metric("Referred / declined",
                   f"{applications['referred']:,} / {applications['declined']:,}", border=True)
    st.caption("An application the API refuses (4xx or 5xx) is a customer who never got a "
               "decision. That is why API errors show up directly in conversion.")

    show_guide()


def start_here(recorded: int) -> None:
    """Three steps for a first visit. Each button does the step or opens the page for it."""
    with st.container(border=True):
        st.write("**Start here** — three steps, about three minutes.")
        cols = st.columns(3)
        with cols[0]:
            st.write("1. **Fill the lab** with healthy traffic, so there is a baseline.")
            if recorded:
                st.caption(f"Done: {recorded:,} requests recorded.")
            elif st.button("Send 150 healthy journeys", key="start_fill", width="stretch"):
                common.generate_traffic("healthy", 150, 7)
                st.rerun()
        with cols[1]:
            st.write("2. **Break something**: switch a fault on and send traffic.")
            st.button("Fault injection", key="start_break", on_click=common.go_to,
                      args=("Fault injection",), width="stretch")
        with cols[2]:
            st.write("3. **See what recurs** and let the agent triage it.")
            st.button("Recurring issues", key="start_recur", on_click=common.go_to,
                      args=("Recurring issues",), width="stretch")


def show_guide() -> None:
    st.subheader("Where to go next")
    for page, description in GUIDE:
        left, right = st.columns([1, 3], vertical_alignment="center")
        left.button(page, on_click=common.go_to, args=(page,), key=f"guide_{page}", width="stretch")
        right.write(description)
