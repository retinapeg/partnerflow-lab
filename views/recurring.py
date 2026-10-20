"""Recurring issues page: which failures keep coming back, how sure we are, and
what the triage agent did about them."""

import json

import pandas as pd
import streamlit as st

from partnerflow import charts, config, recurrence, runbooks
from views import common

REPLAYS = {
    "Revoked API key (cedar) comes back after a fix": {
        "on": [("PUT", "/lab/faults/auth_revoked", {"enabled": True, "partner": "cedar"})],
        "off": [("PUT", "/lab/faults/auth_revoked", {"enabled": False})],
    },
    "Scoring service throws for everyone, twice": {
        "on": [("PUT", "/lab/faults/server_error", {"enabled": True, "partner": "all", "every_nth": 3})],
        "off": [("PUT", "/lab/faults/server_error", {"enabled": False})],
    },
    "Cedar's webhook receiver goes down, recovers, goes down again": {
        "on": [("PUT", "/lab/faults/webhook_failure", {"enabled": True, "partner": "cedar"})],
        "off": [("PUT", "/lab/faults/webhook_failure", {"enabled": False})],
    },
    "Birch ships malformed payloads, fixes it, ships them again": {
        "on": [("PUT", "/lab/faults/bad_payloads", {"enabled": True, "partner": "birch", "share": 0.5})],
        "off": [("PUT", "/lab/faults/bad_payloads", {"enabled": False})],
    },
    "Release B is deployed, rolled back, and deployed again": {
        "on": [("POST", "/lab/release", {"release": "B"})],
        "off": [("POST", "/lab/release", {"release": "A"})],
    },
}

STATUS_HELP = ("active: happening now and statistically real · resolved: was real, now quiet · "
               "watch: happening now but not distinct from the baseline · background: quiet, "
               "never distinct (an intended rule, say)")


def render(lab: dict) -> None:
    st.title("Recurring issues")
    st.write(
        "Every failed request gets a fingerprint: partner, status code, failure reason. "
        "Occurrences with no quiet gap between them form an episode; a fingerprint with two "
        "or more episodes is recurring. Each episode is tested against the history before "
        "it, and the triage agent acts only when the test says the episode is real."
    )

    gap_seconds, clean_requests = parameters()
    replay(lab)

    data = common.get_json("/lab/recurring", gap_seconds=gap_seconds, clean_requests=clean_requests)
    registry = pd.DataFrame(data["registry"])
    episodes = pd.DataFrame(data["episodes"])
    log = common.get_json("/lab/triage", limit=500)

    if registry.empty:
        st.info("No failures recorded yet. Replay a scenario above, or switch a fault on and "
                "send traffic from the Fault injection page.")
        automation(lab, log)
        return

    overview(registry, log)
    chosen = look_closer(registry, episodes)
    agent(chosen, registry, gap_seconds, clean_requests)
    automation(lab, log)
    the_maths()


# --- Controls ------------------------------------------------------------------------

def parameters() -> tuple[int, int]:
    with st.expander("How an episode is defined (the two knobs)"):
        left, right = st.columns(2)
        clean = left.slider("Clean requests that end an episode", 3, 60,
                            value=recurrence.CLEAN_REQUESTS, key="rec_clean",
                            help="Consecutive requests from the same partner to the same endpoints "
                                 "that did not fail this way. Healthy traffic in between is what "
                                 "makes the next occurrence a recurrence.")
        gap = right.slider("Quiet seconds that end an episode", 30, 600,
                           value=recurrence.GAP_SECONDS, step=30, key="rec_gap",
                           help="A lab runs at demo speed, so two minutes here stands in for "
                                "the days a real estate would use.")
        st.caption(f"An episode is flagged when its exact binomial tail probability is below "
                   f"{recurrence.ALPHA:.5f} (the 3-sigma rule) and it has at least "
                   f"{recurrence.MIN_OCCURRENCES} occurrences: one request is an anecdote, "
                   f"three are a pattern.")
    return int(gap), int(clean)


def replay(lab: dict) -> None:
    with st.container(border=True):
        st.write("**Replay a recurring issue** — healthy traffic, the fault, a healthy spell, "
                 "the same fault again. About fifteen seconds; then read the table below.")
        left, right = st.columns([3, 1], vertical_alignment="bottom")
        scenario = left.selectbox("Scenario", list(REPLAYS), key="replay_scenario",
                                  label_visibility="collapsed")
        if right.button("Replay it", type="primary", width="stretch"):
            run_replay(scenario)
            st.rerun()
        if any(fault["enabled"] for fault in lab["faults"].values()) or lab["release"] != "A":
            st.caption("A fault or release B is switched on right now, so the 'healthy' spells "
                       "will not be healthy. Switch them off on the Fault injection page first.")


def run_replay(scenario: str) -> None:
    steps = REPLAYS[scenario]
    log = common.load_log()
    existing = set(log["run_label"].dropna())
    number = 1
    while f"replay-{number}-healthy" in existing:
        number += 1
    label = f"replay-{number}"

    def calls(key: str) -> None:
        for method, path, body in steps[key]:
            common.call_api(method, path, json=body)

    common.generate_traffic(f"{label}-healthy", 60, 7)      # the history the first episode is tested against
    calls("on")
    common.generate_traffic(f"{label}-incident", 40, 8)
    calls("off")
    common.generate_traffic(f"{label}-recovered", 80, 9)
    calls("on")
    common.generate_traffic(f"{label}-again", 40, 10)
    calls("off")
    st.session_state["replay_done"] = label


# --- 1. The registry ---------------------------------------------------------------------

def overview(registry: pd.DataFrame, log: dict) -> None:
    st.header("1. What keeps coming back")
    active = int((registry["status"] == "active").sum())
    recurring = int(registry["recurring"].sum())
    watching = int((registry["status"] == "watch").sum())
    decided = len([e for e in log["entries"] if not e["dry_run"]])
    cols = st.columns(4)
    common.tile(cols[0], "Active issues", active, "real and still happening")
    common.tile(cols[1], "Recurring fingerprints", recurring, "two or more episodes")
    common.tile(cols[2], "Watching", watching, "happening, not yet distinct")
    common.tile(cols[3], "Agent decisions logged", decided, "see the audit log below")

    table = registry.copy()
    table["latest"] = [f"{x} of {n}" for x, n in zip(table["latest_occurrences"], table["latest_exposed"])]
    table["last seen"] = pd.to_datetime(table["last_seen"]).dt.strftime("%H:%M:%S")
    table["endpoints"] = [", ".join(e) for e in table["endpoints"]]
    shown = table[["status", "fingerprint", "class", "owner", "episodes", "occurrences", "latest",
                   "baseline_rate", "tail_p", "trend", "endpoints", "last seen"]]
    common.show_table(
        shown.rename(columns={"baseline_rate": "baseline", "tail_p": "tail p"}),
        percent_columns=("baseline",),
        column_config={"tail p": st.column_config.NumberColumn(format="%.2e"),
                       "status": st.column_config.TextColumn(help=STATUS_HELP)})
    st.caption("Sorted so flagged and open issues come first. Baseline: the fingerprint's share of "
               "exposed traffic before the latest episode. Tail p: how likely the latest episode's "
               "count would be at that baseline.")


# --- 2. One fingerprint in detail -------------------------------------------------------------

def look_closer(registry: pd.DataFrame, episodes: pd.DataFrame) -> dict:
    st.header("2. Look closer")
    labels = [f"{r['fingerprint']}  ({r['status']}, {r['episodes']} episode{'s' if r['episodes'] > 1 else ''})"
              for _, r in registry.iterrows()]
    choice = st.selectbox("Fingerprint", labels, key="rec_choice")
    row = registry.iloc[labels.index(choice)].to_dict()
    mine = episodes[episodes["fingerprint"] == row["fingerprint"]].sort_values("episode")
    partner = config.PARTNER_BY_ID.get(row["partner_id"], {}).get("name", row["partner_id"])

    tail = "no baseline yet" if row["tail_p"] is None else f"{row['tail_p']:.2g}"
    st.write(
        f"**{partner}**: {row['occurrences']} occurrences in {row['episodes']} episode"
        f"{'s' if row['episodes'] > 1 else ''} on {', '.join(row['endpoints'])}. The latest episode "
        f"failed {row['latest_occurrences']} of {row['latest_exposed']} exposed requests "
        f"({common.pct(row['latest_share'])}) against a baseline of {common.pct(row['baseline_rate'], 2)} "
        f"from {row['baseline_clean_requests']} earlier requests: tail probability {tail}. "
        f"Posterior share {common.pct(row['posterior_mean'])} "
        f"({common.pct(row['posterior_low'])} to {common.pct(row['posterior_high'])}). "
        + (f"Trend: {row['trend']} (latest vs earlier, {common.points(row['trend_difference'])}, "
           f"{common.p_value_text(row['trend_p'])}). " if row["trend_p"] is not None else "")
        + (f"Comes back roughly every {row['median_gap_seconds']:.0f} s of lab time." if row["median_gap_seconds"] else "")
    )

    left, right = st.columns([3, 2])
    with left:
        shown = mine.copy()
        shown["start"] = pd.to_datetime(shown["start"]).dt.strftime("%H:%M:%S")
        shown["end"] = pd.to_datetime(shown["end"]).dt.strftime("%H:%M:%S")
        shown["runs"] = [", ".join(r) for r in shown["run_labels"]]
        shown["releases"] = [", ".join(r) for r in shown["release_versions"]]
        common.show_table(
            shown[["episode", "start", "end", "occurrences", "exposed_requests", "share",
                   "baseline_rate", "tail_p", "flagged", "runs", "releases"]]
            .rename(columns={"exposed_requests": "exposed", "baseline_rate": "baseline", "tail_p": "tail p"}),
            percent_columns=("share", "baseline"),
            column_config={"tail p": st.column_config.NumberColumn(format="%.2e")})
    with right:
        chart = mine.assign(label=[f"episode {e}" for e in mine["episode"]])
        st.altair_chart(charts.intervals(chart, "label", "posterior_mean", "posterior_low", "posterior_high",
                                         value_title="share of exposed traffic failing (posterior, 95%)",
                                         reference=float(row["baseline_rate"])),
                        width="stretch")
        st.caption("Dot: posterior mean per episode. Line: 95% interval. Grey rule: the baseline "
                   "the latest episode was tested against.")

    book = runbooks.RUNBOOKS.get(row["runbook"]) or runbooks.lookup(row["status_code"], row["failure_reason"])
    with st.container(border=True):
        st.write(f"**Runbook: {row['runbook']}**  ·  {book['class']}  ·  owner: {book['owner']}  ·  "
                 f"severity when active: {book['severity']}")
        st.write(book["diagnosis"])
        st.write("Check: " + " · ".join(book["checks"]))
        st.write(f"We do: {book['internal_action']}")
        action = book["auto_action"]
        st.caption("The agent may: " + (runbooks.AUTO_ACTIONS[action] if action else "nothing by itself; recommend only."))
    return row


# --- 3. The agent ------------------------------------------------------------------------------

def agent(row: dict, registry: pd.DataFrame, gap_seconds: int, clean_requests: int) -> None:
    st.header("3. Ask the agent")
    st.write("The agent reads the evidence pack for this fingerprint, picks the runbook, decides "
             "**act** or **watch**, drafts the message to the partner, and takes only the action "
             "the runbook allows. Every run is logged.")
    cols = st.columns([1, 1, 2], vertical_alignment="bottom")
    dry = cols[0].toggle("Dry run (decide, do not act)", key="agent_dry")
    mode = cols[1].selectbox("Engine", ["rules", "llm"], key="agent_mode",
                             help="llm: Claude writes the summary and message from the same evidence; "
                                  "needs ANTHROPIC_API_KEY on the API, else falls back to rules.")
    if cols[2].button("Run the agent on this issue", type="primary"):
        result = common.call_api("POST", "/lab/triage", json={
            "mode": mode, "dry_run": dry, "force": True, "fingerprint": row["fingerprint"],
            "gap_seconds": gap_seconds, "clean_requests": clean_requests}).json()
        st.session_state["agent_result"] = result

    result = st.session_state.get("agent_result")
    if not result or not result["triaged"] or result["triaged"][0]["fingerprint"] != row["fingerprint"]:
        return
    entry = result["triaged"][0]
    decision = entry["decision"]
    show = st.error if decision["status"] == "act" and decision["severity"] == "high" else (
        st.warning if decision["status"] == "act" else st.info)
    show(f"**{decision['status'].upper()}** · {decision['class']} · owner: {decision['owner']} · "
         f"severity: {decision['severity']} · blast radius: {decision['blast_radius']} · "
         f"confidence: {decision['confidence']} · engine: {decision['mode']}"
         + (" · DRY RUN" if entry["dry_run"] else ""))
    st.write(decision["summary"])
    if decision.get("llm_note"):
        st.caption(decision["llm_note"])
    if decision.get("guardrail_notes"):
        st.caption("Guardrails: " + "; ".join(decision["guardrail_notes"]))
    left, right = st.columns(2)
    with left:
        st.write("**Message to the partner**")
        if decision["partner_message"]:
            st.code(decision["partner_message"], language=None, wrap_lines=True)
        else:
            st.caption("No message: the agent is watching, not acting.")
    with right:
        st.write("**What we do**")
        st.write(decision["internal_action"])
        st.write("Checks: " + " · ".join(decision["checks"]))
        st.write("**Actions taken**")
        if entry["actions"]:
            for action in entry["actions"]:
                detail = {k: v for k, v in action.items() if k not in ("action", "result", "description")}
                st.write(f"- `{action['action']}`: {action['result']}"
                         + (f" — {detail}" if detail else ""))
        else:
            st.caption("None. The decision was logged.")
    with st.expander("The evidence pack the agent saw"):
        st.json(entry["evidence"], expanded=False)


# --- 4. Automation and the audit log ------------------------------------------------------------

def automation(lab: dict, log: dict) -> None:
    st.header("4. Run it automatically")
    settings = log["auto_triage"]
    st.write("With the timer on, the API runs the agent on its own, acts on new episodes once, "
             "and keeps replaying failed webhooks until they get through. Switch it on, break "
             "something on the Fault injection page, and come back.")
    cols = st.columns([1, 1, 1, 2], vertical_alignment="bottom")
    enabled = cols[0].toggle("Auto-triage", value=bool(settings["enabled"]), key="auto_on")
    interval = cols[1].number_input("Every (seconds)", 5, 600, value=int(settings["interval_seconds"]), key="auto_every")
    mode = cols[2].selectbox("Engine", ["rules", "llm"], index=0 if settings["mode"] == "rules" else 1,
                             key="auto_mode")
    if cols[3].button("Apply"):
        common.call_api("PUT", "/lab/triage/auto",
                        json={"enabled": enabled, "interval_seconds": int(interval), "mode": mode})
        st.rerun()
    if settings["enabled"]:
        st.success(f"Auto-triage is on: every {settings['interval_seconds']} s, engine {settings['mode']}.")
    llm = "available: ANTHROPIC_API_KEY is set on the API" if log["llm_available"] else \
          "not set up: the llm engine falls back to rules until ANTHROPIC_API_KEY is set on the API"
    st.caption(f"Claude engine {llm} (model {log['model']}). From a terminal: `python triage.py --watch 30`.")

    if st.button("Run the agent now on every open episode"):
        st.session_state["agent_all"] = common.call_api("POST", "/lab/triage").json()
        st.rerun()
    done = st.session_state.pop("agent_all", None)
    if done:
        acted = [t for t in done["triaged"] if t["decision"]["status"] == "act"]
        st.info(f"Looked at {done['fingerprints']} fingerprints: acted on {len(acted)}, "
                f"watching {len(done['triaged']) - len(acted)}, skipped {len(done['skipped'])} "
                "(closed or already handled).")

    st.subheader("Audit log")
    entries = log["entries"]
    if not entries:
        st.caption("Nothing decided yet.")
        return
    table = pd.DataFrame([{
        "when": e["timestamp"][11:19],
        "fingerprint": e["fingerprint"],
        "episode": e["episode"],
        "decision": e["status"] + (" (dry run)" if e["dry_run"] else ""),
        "severity": e["decision"].get("severity", ""),
        "owner": e["decision"].get("owner", ""),
        "engine": e["mode"],
        "actions": ", ".join(f"{a['action']}: {a['result']}" for a in e["actions"]) or "-",
        "summary": e["decision"].get("summary", ""),
    } for e in entries])
    common.show_table(table, height=min(400, 40 + 36 * len(table)))
    with st.expander("Full entries (JSON)"):
        st.code(json.dumps(entries[:20], indent=1), language="json")


# --- 5. The maths ------------------------------------------------------------------------------

def the_maths() -> None:
    st.header("5. The statistics on this page")
    with st.expander("Baseline: a Jeffreys estimate from the history before the episode"):
        st.latex(r"p_0 = \frac{x_{\text{before}} + \tfrac{1}{2}}{n_{\text{before}} + 1}")
        st.write("With 300 clean requests and no failures, p0 is 0.17%: 'normal' is not exactly zero, "
                 "we just have not seen it yet. Episodes already recognised as incidents are left "
                 "out of the history, so a recurrence is not mistaken for the new normal; an "
                 "intended rule refusing a steady 5% stays in, so it is not mistaken for an incident.")
    with st.expander("The flag: exact binomial tail, plus a minimum count"):
        st.latex(r"P(X \ge x \mid n, p_0) = \sum_{j=x}^{n} \binom{n}{j} p_0^{\,j} (1-p_0)^{\,n-j} < 0.00135")
        st.write("The same 3-sigma threshold as the Broker monitoring page, with the exact tail "
                 "because counts are small. The minimum of three occurrences is the small-sample "
                 "guard: one 401 in a thousand clean requests has a tiny tail probability and "
                 "means nothing.")
    with st.expander("Posterior share per episode"):
        st.latex(r"p \mid x, n \sim \mathrm{Beta}(x + \tfrac{1}{2},\; n - x + \tfrac{1}{2})")
        st.write("A mean and a 95% interval for the share of exposed traffic failing during the "
                 "episode. Six failures in six requests gives an interval that reaches 100% but a "
                 "mean below it: honest about how little six observations say.")
    with st.expander("Trend: latest episode against the earlier ones"):
        st.latex(r"z = \frac{\hat p_{\text{latest}} - \hat p_{\text{earlier}}}{\sqrt{\bar p(1-\bar p)(1/n_1 + 1/n_2)}}")
        st.write("A two-proportion z-test. 'Worse' means the latest episode fails a larger share "
                 "of exposed traffic than the earlier episodes pooled, at p < 0.05.")
    with st.expander("Why the agent is allowed so little"):
        st.write("It acts only on flagged episodes, only with the runbook's one action, and never "
                 "touches a fault switch or a release. A 5xx is always ours whatever an engine "
                 "says. The audit log keeps the evidence pack with each decision, so a wrong call "
                 "can be traced to the number that caused it. That is what makes it safe to run "
                 "on a timer.")
