"""Fault injection page: switch failures on and watch real behaviour change."""

import pandas as pd
import streamlit as st

from partnerflow import config
from views import common

FAULT_ORDER = ["auth_revoked", "bad_payloads", "missing_resources", "server_error",
               "latency", "rate_limit", "webhook_failure"]
PARTNER_CHOICES = ["all"] + config.PARTNER_IDS

SETTING_LABELS = {
    "partner": "Partner affected",
    "every_nth": "Fail every Nth application (1 = all of them)",
    "delay_ms": "Delay added to each request (milliseconds)",
    "limit": "Requests allowed per window",
    "window_seconds": "Window length (seconds)",
    "share": "Share of that partner's requests affected",
}


def apply_fault(name: str, settings: list[str]) -> None:
    """Widget callback: send the fault's current widget values to the API."""
    body = {setting: st.session_state[f"{name}_{setting}"] for setting in settings}
    common.call_api("PUT", f"/lab/faults/{name}", json=body)


def setting_widget(name: str, setting: str, settings: list[str]) -> None:
    key, label = f"{name}_{setting}", SETTING_LABELS[setting]
    callback = {"on_change": apply_fault, "args": (name, settings)}
    if setting == "partner":
        st.selectbox(label, PARTNER_CHOICES, key=key, **callback)
    elif setting == "share":
        st.slider(label, 0.0, 1.0, step=0.05, key=key, **callback)
    elif setting == "delay_ms":
        st.slider(label, 0, 2000, step=50, key=key, **callback)
    elif setting == "every_nth":
        st.number_input(label, 1, 50, key=key, **callback)
    elif setting == "limit":
        st.number_input(label, 1, 1000, key=key, **callback)
    elif setting == "window_seconds":
        st.number_input(label, 1, 600, key=key, **callback)


def render(lab: dict) -> None:
    st.title("Fault injection")
    st.write(
        "Each switch changes what really happens to requests. Switch one on, send traffic "
        "from the bottom of the page, and read the result. A switch takes effect on the "
        "very next request: the API reads its settings from SQLite every time."
    )
    st.caption("SERVER faults change what the API does. CLIENT faults make the traffic "
               "generator send genuinely bad requests, so the API reacts as it would to a "
               "partner's mistake.")

    for name in FAULT_ORDER:
        fault, info = lab["faults"][name], config.FAULT_INFO[name]
        settings = list(fault)
        # The API is the source of truth: load its values into the widgets on every run.
        for setting in settings:
            st.session_state[f"{name}_{setting}"] = fault[setting]

        with st.container(border=True):
            head, switch = st.columns([4, 1], vertical_alignment="center")
            head.write(f"**{info['label']}**  ·  {info['side'].upper()} fault")
            switch.toggle("Enabled", key=f"{name}_enabled", on_change=apply_fault, args=(name, settings))
            st.write(info["what"])
            st.caption(f"What you should see: {info['expect']}")
            others = [s for s in settings if s != "enabled"]
            for column, setting in zip(st.columns(max(len(others), 2)), others):
                with column:
                    setting_widget(name, setting, settings)
        if name == "rate_limit":
            with st.container(border=True):
                st.write("**G. Validation regression following a release**")
                st.write("A release ships a new validation rule with a bug. This one has its own "
                         "guided page, because finding and fixing it is the main story of the lab.")
                st.button("Open the release simulation", on_click=common.go_to,
                          args=("Release simulation",))

    if any(fault["enabled"] for fault in lab["faults"].values()):
        if st.button("Switch every fault off"):
            for name in FAULT_ORDER:
                common.call_api("PUT", f"/lab/faults/{name}", json={"enabled": False})
            st.rerun()

    st.header("See the effect")
    quick_probe()
    traffic_run(lab)


def quick_probe() -> None:
    st.subheader("Send one request")
    left, right = st.columns([1, 3], vertical_alignment="bottom")
    partner_id = left.selectbox("As partner", config.PARTNER_IDS, key="probe_partner")
    if right.button("Send POST /customers"):
        response = common.call_api(
            "POST", "/customers",
            headers={"X-API-Key": config.PARTNER_BY_ID[partner_id]["api_key"]},
            json={"business_name": "Probe Test Ltd", "contact_email": "probe@test.example",
                  "annual_turnover": 100000, "trading_months": 24})
        body = response.json()
        st.session_state["probe_result"] = {
            "partner": partner_id, "status_code": response.status_code,
            "reason": body["error"]["code"] if "error" in body else "created",
            "latency_ms": round(response.elapsed.total_seconds() * 1000),
            "request_id": response.headers.get("X-Request-ID"),
        }
    probe = st.session_state.get("probe_result")
    if probe:
        show = st.success if probe["status_code"] < 400 else st.error
        show(f"{probe['partner']}: HTTP {probe['status_code']} ({probe['reason']}) in "
             f"{probe['latency_ms']} ms. Request ID {probe['request_id']}.")


def traffic_run(lab: dict) -> None:
    st.subheader("Send a batch of traffic")
    st.write("Sends complete customer journeys through the API: create a customer, apply, "
             "then fetch the application. The same seed always sends the same journeys.")
    log = common.load_log()
    existing = set(log["run_label"].dropna())
    number = 1
    while f"fault-test-{number}" in existing:
        number += 1

    cols = st.columns([2, 1, 1, 1], vertical_alignment="bottom")
    run_label = cols[0].text_input("Name for this run", value=f"fault-test-{number}")
    count = cols[1].number_input("Customer journeys", 10, 500, value=60, step=10)
    seed = cols[2].number_input("Seed", 0, 9999, value=7)
    if cols[3].button("Generate traffic", type="primary"):
        if run_label in existing:
            st.error("That name is already used. Pick a new name so runs stay separate.")
        else:
            st.session_state["fault_run"] = common.generate_traffic(run_label, int(count), int(seed))
            st.rerun()

    summary = st.session_state.get("fault_run")
    if not summary or summary["run_label"] not in existing:
        return
    run = log[log["run_label"] == summary["run_label"]]
    st.write(f"**Result of run '{summary['run_label']}'**")
    cols = st.columns(4)
    cols[0].metric("Requests", f"{len(run):,}", border=True)
    cols[1].metric("Failed", f"{int(run['failed'].sum()):,}", border=True)
    cols[2].metric("Failure rate", common.pct(run["failed"].mean()), border=True)
    cols[3].metric("Latency (95th percentile)", f"{run['latency_ms'].quantile(0.95):.0f} ms", border=True)

    by_partner = run.groupby("partner_id").agg(
        requests=("id", "size"), failed=("failed", "sum"),
        failure_rate=("failed", "mean"),
        median_latency_ms=("latency_ms", "median")).reset_index()
    by_partner["median_latency_ms"] = by_partner["median_latency_ms"].round(1)
    left, right = st.columns(2)
    with left:
        st.write("By partner")
        common.show_table(by_partner, percent_columns=("failure_rate",))
    with right:
        st.write("By status code and reason")
        reasons = (run.assign(failure_reason=run["failure_reason"].fillna("(succeeded)"))
                   .groupby(["status_code", "failure_reason"]).size()
                   .reset_index(name="requests"))
        common.show_table(reasons)

    webhooks = pd.DataFrame(common.get_json("/lab/webhooks", failed_only=True, limit=1000))
    if not webhooks.empty:
        st.write(f"**{len(webhooks)} webhook deliveries failed.** The applications were saved, "
                 "but the partner was never told the decision.")
        common.show_table(webhooks[["timestamp", "application_id", "partner_id", "attempts",
                                    "last_status_code", "failure_reason"]].head(20))
        if st.button("Redeliver failed webhooks"):
            st.session_state["redelivery"] = common.call_api("POST", "/lab/webhooks/redeliver").json()
            st.rerun()
    redelivery = st.session_state.pop("redelivery", None)
    if redelivery:
        st.info(f"Retried {redelivery['retried']}: {redelivery['recovered']} delivered, "
                f"{redelivery['still_failed']} still failing. (Switch the webhook fault off "
                "first if they keep failing.)")
    st.button("Investigate this run", on_click=common.go_to, args=("Incident investigation",))
