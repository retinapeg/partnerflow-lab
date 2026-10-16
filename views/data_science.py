"""Data science page: funnels, partner comparison, correlation vs causation, A/B test.

This page analyses the SYNTHETIC journeys dataset (partnerflow/synthetic.py),
not the live request log. The simulator's true effects are known, so each
method can be checked against the right answer.
"""

import pandas as pd
import streamlit as st

from partnerflow import analytics, charts, synthetic
from views import common


@st.cache_data
def journeys_for(n: int, seed: int) -> pd.DataFrame:
    return synthetic.generate_journeys(n=n, seed=seed)


def render(lab: dict) -> None:
    st.title("Data science")
    st.write(
        "A simulated dataset of fictional customer journeys, from starting an application to "
        "receiving funds. It is separate from the live request log. Because it is simulated, "
        "the true effects are known, so we can see whether each analysis gets the right answer."
    )

    cols = st.columns([1, 2])
    seed = cols[0].number_input("Seed", 0, 9999, value=42,
                                help="The same seed always produces the same dataset.")
    n = cols[1].select_slider("Number of journeys", [500, 1000, 2000, 4000, 8000, 12000, 24000, 48000],
                              value=12000, help="Fewer journeys means wider confidence intervals.")
    journeys = journeys_for(int(n), int(seed))

    with st.expander(f"Look at the data ({len(journeys):,} rows)"):
        st.dataframe(journeys.head(200), hide_index=True)
        st.download_button("Download as CSV", journeys.to_csv(index=False),
                           file_name=f"synthetic_journeys_seed{seed}_n{n}.csv", mime="text/csv")
        st.caption("The two 'would_submit_...' columns are the simulator's ground truth. "
                   "Real data never has them.")

    funnel(journeys)
    partners(journeys)
    errors_and_dropoff(journeys)
    experiment(journeys)
    live_log()


def funnel(journeys: pd.DataFrame) -> None:
    st.header("1. Conversion funnel")
    table = analytics.conversion_funnel(journeys)
    left, right = st.columns([3, 2])
    with left:
        st.altair_chart(charts.bars(table, "stage", "share_of_started", domain=[0, 1],
                                    value_title="share of journeys reaching this stage"),
                        width="stretch")
    with right:
        common.show_table(table, percent_columns=("share_of_started", "step_conversion"))
    worst = table.iloc[1:].sort_values("step_conversion").iloc[0]
    st.write(f"The biggest single loss is at **{worst['stage']}**: only "
             f"{common.pct(worst['step_conversion'])} of journeys that reached the previous "
             "stage get this far.")


def partners(journeys: pd.DataFrame) -> None:
    st.header("2. Comparing partners")
    table = analytics.partner_comparison(journeys)
    left, right = st.columns([2, 3])
    with left:
        st.write("Conversion to funded, with 95% intervals")
        st.altair_chart(charts.intervals(table, "partner", "conversion", "conversion_ci_low",
                                         "conversion_ci_high", value_title="funded / started"),
                        width="stretch")
    with right:
        common.show_table(
            table[["partner", "journeys", "conversion", "submit_rate", "api_error_rate",
                   "median_amount", "latency_ms_p50", "latency_ms_p95"]].round(
                {"latency_ms_p50": 0, "latency_ms_p95": 0}),
            percent_columns=("conversion", "submit_rate", "api_error_rate"))
    best = table.sort_values("conversion").iloc[-1]
    worst = table.sort_values("conversion").iloc[0]
    overlap = worst["conversion_ci_high"] >= best["conversion_ci_low"]
    st.write(
        f"**{worst['partner']}** converts least ({common.pct(worst['conversion'])}) and "
        f"**{best['partner']}** most ({common.pct(best['conversion'])}). Their intervals "
        f"{'overlap, so the gap may be noise' if overlap else 'do not overlap, so the gap is real'}. "
        f"But {worst['partner']} also sends much larger applications (median "
        f"{worst['median_amount']:,.0f} GBP against {best['median_amount']:,.0f}) and has more API "
        "errors. A raw partner ranking mixes all of that together. It says where to look, not why."
    )


def errors_and_dropoff(journeys: pd.DataFrame) -> None:
    st.header("3. Do API errors make customers drop off?")
    result = analytics.error_dropoff_analysis(journeys)
    naive = result["naive"]
    st.write(
        f"Journeys that hit an API error were submitted {common.pct(naive['rate_b'])} of the time; "
        f"journeys with no error, {common.pct(naive['rate_a'])}. Tempting conclusion: errors cost "
        f"{abs(naive['difference']) * 100:.1f} points of completion. That is a **correlation**."
    )

    estimates = pd.DataFrame([
        {"estimate": "Naive: all journeys with vs without an error",
         "effect": naive["difference"], "low": naive["ci_low"], "high": naive["ci_high"]},
        {"estimate": "Adjusted: compared within partner and amount band",
         "effect": result["adjusted_difference"], "low": result["adjusted_ci_low"],
         "high": result["adjusted_ci_high"]},
    ])
    st.altair_chart(charts.intervals(estimates, "estimate", "effect", "low", "high",
                                     value_format="+.1%", reference=result["true_effect"],
                                     value_title="change in submit rate when a journey has an API error "
                                                 "(grey line = true effect in the simulator)"),
                    width="stretch")
    cols = st.columns(3)
    cols[0].metric("Naive difference", common.points(naive["difference"]), border=True)
    cols[1].metric("Adjusted difference", common.points(result["adjusted_difference"]), border=True)
    cols[2].metric("True causal effect", common.points(result["true_effect"]), border=True,
                   help="Known only because the data is simulated.")
    st.write(
        "**Why they differ.** Large applications have more API errors (more documents, more "
        "calls) and are also abandoned more often for unrelated reasons (longer forms). Amount "
        "is a *confounder*: it pushes both numbers. The naive comparison blames errors for "
        "drop-off that would have happened anyway. Comparing like with like, inside each "
        "partner and amount band, removes most of that."
    )
    st.write(
        "**The limit of adjusting.** It worked here because the simulator's only confounders "
        "are partner and amount band, and both are in the data. Real data has confounders "
        "nobody recorded. An observational analysis like this is evidence, not proof. For "
        "proof of cause you need to control who gets what, which is the experiment below."
    )
    with st.expander("The comparison inside each partner and amount band"):
        common.show_table(result["strata"], percent_columns=(
            "submit_rate_with_error", "submit_rate_without_error", "difference"))


def experiment(journeys: pd.DataFrame) -> None:
    st.header("4. A randomised A/B experiment")
    st.write(
        "Fictional experiment: half of journeys (chosen by coin flip) see clearer inline error "
        "messages in the application form. Because chance alone decides who is treated, the two "
        "groups are alike in every other way, so a difference in outcome is caused by the treatment."
    )
    metric = st.radio("Outcome to measure", ["submitted", "funded"], horizontal=True,
                      format_func=lambda m: {"submitted": "Application submitted (primary)",
                                             "funded": "Loan funded (further downstream)"}[m])
    result = analytics.ab_test(journeys, metric)

    cols = st.columns(3)
    common.tile(cols[0], "Control conversion", common.pct(result["control_rate"]),
                f"{result['control_conversions']:,} of {result['control_n']:,}")
    common.tile(cols[1], "Treatment conversion", common.pct(result["treatment_rate"]),
                f"{result['treatment_conversions']:,} of {result['treatment_n']:,}")
    common.tile(cols[2], "Sample size", f"{result['sample_size']:,}", "journeys in the experiment")
    cols = st.columns(2)
    common.tile(cols[0], "Absolute treatment effect", common.points(result["absolute_effect"]),
                "treatment conversion minus control conversion")
    common.tile(cols[1], "95% confidence interval",
                f"{result['ci_low'] * 100:+.1f} to {result['ci_high'] * 100:+.1f} pts",
                "the range of effects the data is consistent with")

    effect = pd.DataFrame([{"estimate": "Treatment minus control", "effect": result["absolute_effect"],
                            "low": result["ci_low"], "high": result["ci_high"]}])
    st.altair_chart(charts.intervals(effect, "estimate", "effect", "low", "high",
                                     value_format="+.1%", reference=0.0,
                                     value_title=f"effect on '{metric}' (grey line = no effect)"),
                    width="stretch")
    st.write(f"**Reading the result.** {analytics.explain_ab(result)} "
             f"({common.p_value_text(result['p_value'])}.)")
    if metric == "submitted":
        covered = result["ci_low"] <= synthetic.TRUE_AB_LIFT <= result["ci_high"]
        st.write(f"**Check against the truth.** The simulator's real lift on submission is "
                 f"{common.points(synthetic.TRUE_AB_LIFT)}. The interval "
                 f"{'contains' if covered else 'misses'} it. Roughly 1 seed in 20 will miss: "
                 "that is what 95% means.")
    else:
        st.write("**Why this one is noisier.** The treatment acts on submission. Funding happens "
                 "three stages later, so the same lift is diluted by every later drop-off while "
                 "the noise stays. Downstream metrics need larger samples.")
    st.caption("Try a smaller number of journeys at the top of the page and watch the interval "
               "widen until it includes zero.")


def live_log() -> None:
    st.header("5. The same statistics on the live request log")
    log = common.load_log()
    if log.empty:
        st.caption("The request log is empty. Run the release simulation to fill it.")
        return
    rates = analytics.error_rates(log, "release_version")
    st.write("API failure rate by release, from requests the API really handled:")
    common.show_table(rates, percent_columns=("failure_rate", "ci_low", "ci_high"))
    changes = analytics.detect_release_changes(log)
    flagged = changes[changes["flagged"]] if not changes.empty else changes
    if flagged.empty:
        st.caption("No significant change between releases has been detected yet.")
    else:
        st.write("Significant changes between consecutive releases:")
        common.show_table(common.with_p_text(flagged)[
                              ["request", "from_release", "to_release", "failure_rate_before",
                               "failure_rate_after", "change", "p_value"]],
                          percent_columns=("failure_rate_before", "failure_rate_after", "change"))
    st.button("Open the incident investigation", on_click=common.go_to,
              args=("Incident investigation",))
