"""Incident investigation page: filter the request log, find patterns, trace one request."""

import pandas as pd
import streamlit as st

from partnerflow import analytics, charts, explain
from views import common

DIMENSIONS = {
    "Failure reason": "failure_reason",
    "Partner": "partner_id",
    "Endpoint": "request",
    "Release version": "release_version",
    "Product": "product",
    "Amount band": "amount_band",
    "Traffic run": "run_label",
}
TABLE_COLUMNS = ["timestamp", "request_id", "run_label", "partner_id", "request", "status_code",
                 "failure_reason", "latency_ms", "release_version", "product", "amount"]


def render(lab: dict) -> None:
    st.title("Incident investigation")
    st.write("Start broad, then narrow down: how many requests failed, where the failures "
             "concentrate, what changed between releases, and finally the full record of a "
             "single request.")

    log = common.load_log()
    if log.empty:
        st.info("The request log is empty. Send some traffic first.")
        st.button("Go to fault injection", on_click=common.go_to, args=("Fault injection",))
        return

    filtered = filters(log)
    summary(filtered)
    concentration(filtered)
    release_changes(log)
    trace(log, filtered)
    webhooks()


def filters(log: pd.DataFrame) -> pd.DataFrame:
    st.subheader("1. Choose what to look at")
    cols = st.columns(4)
    runs = cols[0].multiselect("Traffic run", sorted(log["run_label"].dropna().unique()),
                               help="Leave empty for all runs, including requests with no run name.")
    partners = cols[1].multiselect("Partner", sorted(log["partner_id"].dropna().unique()))
    releases = cols[2].multiselect("Release version", sorted(log["release_version"].unique()))
    endpoints = cols[3].multiselect("Endpoint", sorted(log["request"].unique()))
    filtered = log
    for column, chosen in (("run_label", runs), ("partner_id", partners),
                           ("release_version", releases), ("request", endpoints)):
        if chosen:
            filtered = filtered[filtered[column].isin(chosen)]
    return filtered


def summary(filtered: pd.DataFrame) -> None:
    failed = int(filtered["failed"].sum())
    cols = st.columns(4)
    cols[0].metric("Requests", f"{len(filtered):,}", border=True)
    cols[1].metric("Failed (400 and above)", f"{failed:,}", border=True)
    cols[2].metric("Failure rate", common.pct(filtered["failed"].mean()) if len(filtered) else "-",
                   border=True)
    cols[3].metric("Server errors (500s)", f"{int((filtered['status_code'] >= 500).sum()):,}",
                   border=True, help="4xx means the caller must change something. "
                                     "5xx means the API itself failed.")


def concentration(filtered: pd.DataFrame) -> None:
    st.subheader("2. Where do failures concentrate?")
    if filtered.empty or not filtered["failed"].any():
        st.success("No failures in this selection.")
        return
    name = st.selectbox("Break down by", list(DIMENSIONS))
    column = DIMENSIONS[name]

    if column == "failure_reason":
        failures = filtered[filtered["failed"]]
        table = (failures.groupby(["status_code", "failure_reason"]).size()
                 .reset_index(name="requests").sort_values("requests", ascending=False))
        table["what it means"] = [explain.explain(code, reason)["meaning"]
                                  for code, reason in zip(table["status_code"], table["failure_reason"])]
        table["where it comes from"] = [explain.explain(code, reason)["origin"]
                                        for code, reason in zip(table["status_code"], table["failure_reason"])]
        common.show_table(table)
        return

    rows = filtered.dropna(subset=[column])
    if rows.empty:
        st.caption(f"No requests in this selection carry a {name.lower()}.")
        return
    table, test = analytics.failure_concentration(rows, column)
    left, right = st.columns([3, 2])
    with left:
        common.show_table(
            table[[column, "requests", "failures", "failure_rate", "share_of_requests",
                   "share_of_failures", "lift"]].round({"lift": 2}),
            percent_columns=("failure_rate", "share_of_requests", "share_of_failures"))
        st.caption("Lift = this group's failure rate divided by the overall rate. "
                   "Above 1 means it fails more than its share of traffic predicts.")
    with right:
        top = table.iloc[0][column]
        st.altair_chart(charts.bars(table, column, "failure_rate", highlight=[top],
                                    value_title="failure rate"), width="stretch")
    if test["p_value"] is not None:
        verdict = ("unlikely to be chance" if test["p_value"] < 0.01
                   else "could plausibly be chance")
        caveat = "" if test["valid"] else " Some groups are small, so treat this as a rough guide."
        st.write(f"Chi-square test that every group fails at the same rate: "
                 f"**{common.p_value_text(test['p_value'])}**. The differences are {verdict}.{caveat}")


def release_changes(log: pd.DataFrame) -> None:
    st.subheader("3. What changed between releases?")
    by_release = analytics.error_rates(log, "release_version")
    if len(by_release) < 2:
        st.caption("Only one release appears in the log so far, so there is nothing to compare.")
        return
    changes = analytics.detect_release_changes(log)
    st.write("Each release is compared with the one before it, endpoint by endpoint. "
             "A change is flagged when it is too large to be chance (p below 0.01).")
    common.show_table(
        changes[["request", "from_release", "to_release", "requests_before", "failure_rate_before",
                 "requests_after", "failure_rate_after", "change", "ci_low", "ci_high", "flagged"]],
        percent_columns=("failure_rate_before", "failure_rate_after", "change", "ci_low", "ci_high"))
    st.caption("This only says the failure rate changed when the release changed. If the traffic "
               "mix changed at the same moment, that could be the cause instead. The release "
               "simulation rules that out by replaying identical traffic.")


def trace(log: pd.DataFrame, filtered: pd.DataFrame) -> None:
    st.subheader("4. Trace a single request")
    only_failures = st.toggle("Show failed requests only", value=True)
    rows = filtered[filtered["failed"]] if only_failures else filtered
    rows = rows.sort_values("id", ascending=False).head(500)
    st.write(f"Newest first ({len(rows)} shown). Click a row to open it, or paste a request ID.")
    event = common.show_table(rows[TABLE_COLUMNS], height=260, on_select="rerun",
                              selection_mode="single-row", key="trace_table")
    typed = st.text_input("Request ID", placeholder="e.g. the X-Request-ID from a response")

    request_id = typed.strip()
    if not request_id and event.selection.rows:
        request_id = rows.iloc[event.selection.rows[0]]["request_id"]
    if not request_id:
        return

    response = common.call_api("GET", f"/lab/requests/{request_id}")
    if response.status_code != 200:
        st.error(f"No recorded request has the ID '{request_id}'.")
        return
    entry = response.json()
    guide = explain.explain(entry["status_code"], entry["failure_reason"])

    with st.container(border=True):
        st.write(f"**{entry['method']} {entry['path']}**  ·  request `{entry['request_id']}`")
        cols = st.columns(4)
        cols[0].metric("Status code", entry["status_code"])
        cols[1].metric("Latency", f"{entry['latency_ms']:.1f} ms")
        cols[2].metric("Partner", entry["partner_id"] or "unknown")
        cols[3].metric("Release", entry["release_version"])
        if entry["status_code"] >= 400:
            st.error(f"**{entry['failure_reason']}**: {guide['meaning']}")
            st.write(f"**Where it came from:** {guide['origin']}")
            st.write(f"**What to check next:** {guide['next_step']}")
        else:
            st.success(guide["meaning"])
        st.write("Full record:")
        st.json(entry)

        journey = entry["metadata"].get("journey")
        if journey is not None and entry["run_label"]:
            steps = log[(log["run_label"] == entry["run_label"]) & (log["journey"] == journey)]
            st.write(f"**The rest of this customer's journey** (run '{entry['run_label']}', "
                     f"journey {journey}):")
            common.show_table(steps[["timestamp", "request_id", "request", "status_code",
                                     "failure_reason", "latency_ms"]])
            same = log[(log["journey"] == journey) & (log["request"] == entry["method"] + " " + entry["endpoint"])
                       & (log["run_label"] != entry["run_label"])]
            if not same.empty:
                st.write("**The identical request in other runs:**")
                common.show_table(same[["run_label", "release_version", "request_id",
                                        "status_code", "failure_reason"]])


def webhooks() -> None:
    deliveries = pd.DataFrame(common.get_json("/lab/webhooks", limit=5000))
    if deliveries.empty:
        return
    st.subheader("5. Webhook deliveries")
    failed = deliveries[deliveries["status"] == "failed"]
    st.write(f"{len(deliveries):,} deliveries recorded, **{len(failed):,} failed**. A failed "
             "webhook does not fail the API request, so it never appears as an HTTP error above.")
    if not failed.empty:
        common.show_table(failed.head(50))
        if st.button("Redeliver failed webhooks", key="investigation_redeliver"):
            result = common.call_api("POST", "/lab/webhooks/redeliver").json()
            st.session_state["investigation_redelivery"] = result
            st.rerun()
    result = st.session_state.pop("investigation_redelivery", None)
    if result:
        st.info(f"Retried {result['retried']}: {result['recovered']} delivered, "
                f"{result['still_failed']} still failing.")
