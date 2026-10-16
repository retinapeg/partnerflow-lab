"""Release simulation page: a guided walk from healthy, to broken, to fixed.

Every number here is computed from the request log. The page works out which
step you are on by looking at which traffic runs exist and which release is live.
"""

import inspect

import pandas as pd
import streamlit as st

from partnerflow import analytics, charts, config, rules
from views import common

BEFORE, AFTER, FIXED = common.RUN_ORDER
SHOULD_STAY_ELIGIBLE = [p for p in config.PRODUCTS if p != config.R204_PRODUCT]


def step(number: int, title: str, done: bool):
    """A bordered box for one step, with a tick once it is complete."""
    box = st.container(border=True)
    mark = ":material/check_circle:" if done else ":material/radio_button_unchecked:"
    box.subheader(f"{mark} Step {number}: {title}")
    return box


def failure_rate(posts: pd.DataFrame, run_label: str, products: list[str] | None = None) -> dict:
    rows = posts[posts["run_label"] == run_label]
    if products is not None:
        rows = rows[rows["product"].isin(products)]
    return {"sent": len(rows), "failed": int(rows["failed"].sum()),
            "rate": rows["failed"].mean() if len(rows) else float("nan")}


def render(lab: dict) -> None:
    st.title("Release simulation")
    st.write(
        "A new API release goes out and applications start failing. Work through the steps "
        "to measure the damage, find the cause, fix it without removing the rule the release "
        "was meant to add, and prove the fix worked."
    )

    log = common.load_log()
    posts = log[log["request"] == "POST /applications"]
    have = {label: bool((log["run_label"] == label).any()) for label in common.RUN_ORDER}
    band_low, band_high = lab["r204_band"]

    with st.expander("Traffic settings", expanded=False):
        cols = st.columns(2)
        count = cols[0].number_input("Customer journeys per run", 30, 500, value=150, step=10,
                                     disabled=have[BEFORE])
        seed = cols[1].number_input("Seed", 0, 9999, value=7, disabled=have[BEFORE])
        st.caption("All three runs use the same seed, so they send exactly the same journeys. "
                   "That is what makes the comparison fair. Locked once step 1 has run.")
        if any(have.values()) and st.button("Start the story again (reset the lab)"):
            common.call_api("POST", "/lab/reset")
            st.rerun()

    # ---- Step 1 --------------------------------------------------------------
    with step(1, "Send traffic before the release", have[BEFORE]):
        st.write("Record a baseline while the API is on **v1.0.0**.")
        if have[BEFORE]:
            before = failure_rate(posts, BEFORE)
            st.write(f"Baseline recorded: **{before['sent']}** applications sent, "
                     f"**{before['failed']}** refused ({common.pct(before['rate'])}). "
                     "Those few are legitimate: amounts outside the product limits (rule R-101).")
        elif lab["version"] != "v1.0.0":
            st.warning(f"The API is on {lab['version']}. Reset the lab to start from v1.0.0.")
        elif st.button("Generate 'before-release' traffic", type="primary"):
            common.generate_traffic(BEFORE, int(count), int(seed))
            st.rerun()

    # ---- Step 2 --------------------------------------------------------------
    release_live = lab["release"] == "B"
    with step(2, "Deploy release v1.1.0", release_live):
        st.write("**Release notes for v1.1.0:**")
        st.info(config.RELEASE_NOTES["v1.1.0"] + f" Band: {band_low:,} to {band_high:,} GBP.")
        if release_live:
            st.write("Release B is live.")
        elif st.button("Activate release v1.1.0", type="primary", disabled=not have[BEFORE]):
            common.call_api("POST", "/lab/release", json={"release": "B"})
            st.rerun()

    # ---- Step 3 --------------------------------------------------------------
    with step(3, "Send the same traffic after the release", have[AFTER]):
        st.write("Identical journeys, so any change in results comes from the release.")
        if have[AFTER]:
            st.write("Recorded.")
        elif st.button("Generate 'after-release' traffic", type="primary",
                       disabled=not (have[BEFORE] and lab["version"] == "v1.1.0")):
            common.generate_traffic(AFTER, int(count), int(seed))
            st.rerun()

    if not have[AFTER]:
        st.caption("Steps 4 to 8 appear once the 'after-release' traffic exists.")
        return

    # ---- Step 4 --------------------------------------------------------------
    change = analytics.compare_failure_rates(posts, "run_label", BEFORE, AFTER)
    with step(4, "Observe the failure rate", True):
        cols = st.columns(3)
        common.tile(cols[0], "Refused before release", common.pct(change["rate_a"]),
                    f"{change['successes_a']} of {change['n_a']} applications")
        common.tile(cols[1], "Refused after release", common.pct(change["rate_b"]),
                    f"{change['successes_b']} of {change['n_b']} applications")
        common.tile(cols[2], "Change", common.points(change["difference"]),
                    f"95% interval {common.points(change['ci_low'])} to {common.points(change['ci_high'])}")
        if change["ci_low"] > 0:
            st.error(f"The refusal rate for POST /applications rose by "
                     f"{common.points(change['difference'])} ({common.p_value_text(change['p_value'])}). "
                     "The interval excludes zero, so this is not noise.")
        else:
            st.success("No clear increase in refusals between the two runs.")
        rates = pd.DataFrame([{"run": label, "refusal rate": failure_rate(posts, label)["rate"]}
                              for label in common.RUN_ORDER if have[label]])
        st.altair_chart(charts.bars(rates, "run", "refusal rate", domain=[0, 1],
                                    value_title="share of applications refused"), width="stretch")
        detected = analytics.detect_release_changes(log)
        flagged = detected[detected["flagged"] & (detected["change"] > 0)]
        if not flagged.empty:
            st.write("Automatic check across every endpoint (flagged = significant rise):")
            common.show_table(common.with_p_text(flagged)[
                                  ["request", "from_release", "to_release",
                                   "failure_rate_before", "failure_rate_after", "p_value"]],
                              percent_columns=("failure_rate_before", "failure_rate_after"))

    # ---- Step 5 --------------------------------------------------------------
    flips = analytics.paired_flips(log, BEFORE, AFTER)
    with step(5, "Inspect which requests failed", True):
        st.write(f"Because both runs sent the same journeys, they can be matched one to one. "
                 f"**{len(flips)}** applications were accepted before the release and refused after it:")
        common.show_table(flips.rename(columns={"failure_reason_after": "reason_after",
                                                "request_id_after": "request_id"}), height=220)
        if not flips.empty:
            request_id = st.selectbox("Open one of these requests", flips["request_id_after"])
            st.json(common.get_json(f"/lab/requests/{request_id}"), expanded=False)
            st.button("See all failures in Incident investigation", on_click=common.go_to,
                      args=("Incident investigation",))

    # ---- Step 6 --------------------------------------------------------------
    affected = sorted(flips["product"].dropna().unique())
    unexpected = [product for product in affected if product != config.R204_PRODUCT]
    with step(6, "Identify the cause", True):
        if flips.empty:
            st.write("No request changed outcome, so there is nothing to explain.")
        else:
            reasons = ", ".join(sorted(flips["failure_reason_after"].unique()))
            st.write(f"Every newly refused request has the reason **{reasons}**, and every amount "
                     f"is between **{int(flips['amount'].min()):,}** and **{int(flips['amount'].max()):,}** GBP. "
                     "The grid shows where refusals concentrate after the release:")
            after_posts = posts[posts["run_label"] == AFTER]
            grid = (after_posts.dropna(subset=["product", "amount_band"])
                    .groupby(["product", "amount_band"])["failed"].mean().reset_index(name="refusal rate"))
            st.altair_chart(charts.heatmap(grid, "amount_band", "product", "refusal rate",
                                           x_order=analytics.AMOUNT_BAND_LABELS,
                                           y_order=list(config.PRODUCTS)), width="stretch")
            st.caption("Each cell is the share of applications refused, for one product and "
                       "one amount band, after the release.")
            _, test = analytics.failure_concentration(after_posts.dropna(subset=["amount_band"]),
                                                      "amount_band")
            if test["p_value"] is not None:
                st.caption(f"Chi-square test that every amount band fails at the same rate: "
                           f"{common.p_value_text(test['p_value'])}. The concentration is not chance.")
        if unexpected:
            st.error(f"The release notes say R-204 applies to **{config.R204_PRODUCT}** only. "
                     f"The log shows it also refusing **{', '.join(unexpected)}**. "
                     "The rule is being applied to products it was never meant for.")
        elif affected:
            st.success(f"R-204 is only refusing {config.R204_PRODUCT}, as the release notes intend.")
        with st.expander("Read the rule's source code (partnerflow/rules.py)"):
            st.code(inspect.getsource(rules.check_r204_amount_band), language="python")
            st.caption("In v1.1.0 the product check is missing from the condition.")

    # ---- Step 7 --------------------------------------------------------------
    fixed_live = lab["version"] == "v1.1.1"
    with step(7, "Apply a targeted fix", fixed_live):
        st.write(
            "Three options, and why only one is right:\n"
            "- **Roll back to v1.0.0.** Stops the damage, but also removes the rule the release "
            "was meant to add.\n"
            "- **Switch R-204 off.** Same problem: it bypasses an intended eligibility rule.\n"
            "- **Patch v1.1.1.** Restore the missing product check. The bug goes, the rule stays."
        )
        st.info("**Release notes for v1.1.1:** " + config.RELEASE_NOTES["v1.1.1"])
        if fixed_live:
            st.write("Patch v1.1.1 is live.")
        elif st.button("Apply patch v1.1.1", type="primary", disabled=lab["release"] != "B"):
            common.call_api("POST", "/lab/hotfix", json={"enabled": True})
            st.rerun()

    # ---- Step 8 --------------------------------------------------------------
    with step(8, "Verify the fix with the same traffic", have[FIXED]):
        if not have[FIXED]:
            st.write("Send the identical journeys once more and compare all three runs.")
            if st.button("Generate 'after-fix' traffic", type="primary", disabled=not fixed_live):
                common.generate_traffic(FIXED, int(count), int(seed))
                st.rerun()
            return

        rows = []
        for group, products in (("should stay eligible: " + ", ".join(SHOULD_STAY_ELIGIBLE),
                                 SHOULD_STAY_ELIGIBLE),
                                (f"covered by R-204: {config.R204_PRODUCT}", [config.R204_PRODUCT])):
            row = {"products": group, "applications sent": failure_rate(posts, BEFORE, products)["sent"]}
            for label in common.RUN_ORDER:
                row[f"refused {label}"] = failure_rate(posts, label, products)["failed"]
            rows.append(row)
        common.show_table(pd.DataFrame(rows))

        eligible = {label: failure_rate(posts, label, SHOULD_STAY_ELIGIBLE) for label in common.RUN_ORDER}
        rates = pd.DataFrame([{"run": label, "refusal rate": eligible[label]["rate"]}
                              for label in common.RUN_ORDER])
        st.write(f"Refusal rate for {', '.join(SHOULD_STAY_ELIGIBLE)}:")
        st.altair_chart(charts.bars(rates, "run", "refusal rate", domain=[0, 1],
                                    value_title="share of applications refused"), width="stretch")

        still_flipped = analytics.paired_flips(log, BEFORE, FIXED)
        wrongly_refused = still_flipped[still_flipped["product"] != config.R204_PRODUCT]
        if wrongly_refused.empty and eligible[FIXED]["failed"] == eligible[BEFORE]["failed"]:
            st.success(
                f"Restored. {', '.join(SHOULD_STAY_ELIGIBLE)}: {eligible[BEFORE]['failed']} refused "
                f"before the release, {eligible[AFTER]['failed']} after it, "
                f"{eligible[FIXED]['failed']} after the fix. Every application that the bug "
                "refused is accepted again.")
        else:
            st.error(f"{len(wrongly_refused)} eligible applications are still being refused.")
        kept = still_flipped[still_flipped["product"] == config.R204_PRODUCT]
        kept_before_fix = flips[flips["product"] == config.R204_PRODUCT]
        st.info(
            f"The intended rule is still enforced: {len(kept)} {config.R204_PRODUCT} applications "
            f"in the {band_low:,} to {band_high:,} band are refused under v1.1.1 "
            f"({len(kept_before_fix)} under v1.1.0). So the overall refusal rate does not return "
            "to the v1.0.0 baseline, and it should not: that difference is the policy, not the bug.")
