"""The release simulation: reproduce the v1.1.0 regression, then prove the fix.

Release A  = v1.0.0  baseline
Release B  = v1.1.0  adds rule R-204, with a bug (it ignores the product)
B + hotfix = v1.1.1  R-204 applies to invoice_finance only, as intended
"""

import pytest

from partnerflow import analytics, traffic
from tests.helpers import apply, log_entry, new_customer

PRODUCTS = ["term_loan", "credit_line", "invoice_finance"]
SHOULD_STAY_ELIGIBLE = ["term_loan", "credit_line"]
IN_BAND = 30_000        # inside the default 25,000-50,000 band
OUT_OF_BAND = 12_000


def release(client, name):
    assert client.post("/lab/release", json={"release": name}).status_code == 200


def hotfix(client, enabled=True):
    assert client.post("/lab/hotfix", json={"enabled": enabled}).status_code == 200


@pytest.fixture
def customer(client, alder):
    return new_customer(client, alder, annual_turnover=2_000_000)


# --- Single-request behaviour -----------------------------------------------

@pytest.mark.parametrize("product", PRODUCTS)
def test_release_a_accepts_in_band_amounts_for_every_product(client, alder, customer, product):
    assert apply(client, alder, customer["id"], product, IN_BAND).status_code == 201


@pytest.mark.parametrize("product", PRODUCTS)
def test_release_b_rejects_in_band_amounts_for_every_product(client, alder, customer, product):
    """This is the regression: term_loan and credit_line should NOT be rejected."""
    release(client, "B")
    response = apply(client, alder, customer["id"], product, IN_BAND)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rule_R204_amount_band"
    entry = log_entry(client, response)
    assert entry["release_version"] == "v1.1.0"
    assert entry["failure_reason"] == "rule_R204_amount_band"
    assert entry["metadata"]["rule_id"] == "R-204"
    assert entry["metadata"]["product"] == product
    assert entry["metadata"]["amount"] == IN_BAND


@pytest.mark.parametrize("product", PRODUCTS)
def test_release_b_leaves_out_of_band_amounts_alone(client, alder, customer, product):
    release(client, "B")
    assert apply(client, alder, customer["id"], product, OUT_OF_BAND).status_code == 201


@pytest.mark.parametrize("amount, accepted", [
    (24_500, True), (25_000, False), (50_000, False), (50_500, True)])
def test_band_edges_are_inclusive(client, alder, customer, amount, accepted):
    release(client, "B")
    status_code = apply(client, alder, customer["id"], "term_loan", amount).status_code
    assert status_code == (201 if accepted else 422)


# --- The targeted fix -------------------------------------------------------

@pytest.mark.parametrize("product", SHOULD_STAY_ELIGIBLE)
def test_hotfix_restores_products_that_should_stay_eligible(client, alder, customer, product):
    release(client, "B")
    hotfix(client)
    response = apply(client, alder, customer["id"], product, IN_BAND)
    assert response.status_code == 201
    assert response.json()["release_version"] == "v1.1.1"


def test_hotfix_keeps_the_intended_rule_for_invoice_finance(client, alder, customer):
    """The fix must not bypass the rule it was meant to enforce."""
    release(client, "B")
    hotfix(client)
    response = apply(client, alder, customer["id"], "invoice_finance", IN_BAND)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rule_R204_amount_band"
    assert apply(client, alder, customer["id"], "invoice_finance", OUT_OF_BAND).status_code == 201


@pytest.mark.parametrize("release_name, with_hotfix", [("A", False), ("B", False), ("B", True)])
def test_product_limits_are_enforced_in_every_release(client, alder, customer, release_name, with_hotfix):
    """R-101 is an intended limit. No release or fix switches it off."""
    release(client, release_name)
    if with_hotfix:
        hotfix(client)
    response = apply(client, alder, customer["id"], "credit_line", 150_000)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rule_R101_product_limits"


def test_removing_the_hotfix_brings_the_bug_back(client, alder, customer):
    release(client, "B")
    hotfix(client)
    assert apply(client, alder, customer["id"], "term_loan", IN_BAND).status_code == 201
    hotfix(client, enabled=False)
    assert apply(client, alder, customer["id"], "term_loan", IN_BAND).status_code == 422


def test_the_band_is_configurable(client, alder, customer):
    release(client, "B")
    client.put("/lab/r204-band", json={"min_amount": 60_000, "max_amount": 80_000})
    assert apply(client, alder, customer["id"], "term_loan", IN_BAND).status_code == 201
    assert apply(client, alder, customer["id"], "term_loan", 70_000).status_code == 422
    assert client.put("/lab/r204-band", json={"min_amount": 9, "max_amount": 1}).status_code == 422


# --- The whole story with generated traffic ---------------------------------

def run_release_story(client, count=150, seed=7) -> dict:
    """Before release, after release, after fix: identical traffic each time."""
    before = traffic.generate("before-release", count, seed, client)
    release(client, "B")
    after = traffic.generate("after-release", count, seed, client)
    hotfix(client)
    fixed = traffic.generate("after-fix", count, seed, client)
    return {"before": before, "after": after, "fixed": fixed}


@pytest.fixture(scope="module")
def story(tmp_path_factory):
    """Run the release story once and share its request log between tests."""
    from fastapi.testclient import TestClient

    import main
    from partnerflow import faults

    db_file = tmp_path_factory.mktemp("story") / "lab.db"
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PARTNERFLOW_DB", str(db_file))
        faults.reset_runtime_state()
        with TestClient(main.app) as client:
            summaries = run_release_story(client)
        log = analytics.load_requests(db_file)
    return {"summaries": summaries, "log": log,
            "posts": log[log["request"] == "POST /applications"]}


def failures(posts, run_label, products):
    rows = posts[(posts["run_label"] == run_label) & posts["product"].isin(products)]
    return int(rows["failed"].sum())


def test_regression_is_reproduced_with_generated_traffic(story):
    summaries, log, posts = story["summaries"], story["log"], story["posts"]

    # 1. The same 150 applications were sent each time.
    assert [summaries[run]["applications_sent"] for run in ("before", "after", "fixed")] == [150] * 3

    # 2. The failure rate of POST /applications rose sharply, beyond chance.
    change = analytics.compare_failure_rates(posts, "run_label", "before-release", "after-release")
    assert change["rate_b"] > change["rate_a"] + 0.15
    assert change["ci_low"] > 0
    assert change["p_value"] < 0.001

    # 3. Every request that newly failed was an in-band R-204 rejection...
    flips = analytics.paired_flips(log, "before-release", "after-release")
    assert len(flips) > 20
    assert set(flips["failure_reason_after"]) == {"rule_R204_amount_band"}
    assert flips["amount"].between(25_000, 50_000).all()
    # ...and it hit products the release notes said were unaffected.
    assert set(SHOULD_STAY_ELIGIBLE) <= set(flips["product"])


def test_regression_is_resolved_by_the_targeted_fix(story):
    log, posts = story["log"], story["posts"]

    # Products that should stay eligible: broken by the release, fully restored by the fix.
    before = failures(posts, "before-release", SHOULD_STAY_ELIGIBLE)
    assert failures(posts, "after-release", SHOULD_STAY_ELIGIBLE) > before
    assert failures(posts, "after-fix", SHOULD_STAY_ELIGIBLE) == before
    eligible_only = log[log["product"].isin(SHOULD_STAY_ELIGIBLE)]
    assert analytics.paired_flips(eligible_only, "before-release", "after-fix").empty

    # invoice_finance: the intended rule is still enforced after the fix.
    remaining = analytics.paired_flips(log, "before-release", "after-fix")
    assert len(remaining) > 0
    assert set(remaining["product"]) == {"invoice_finance"}
    assert set(remaining["failure_reason_after"]) == {"rule_R204_amount_band"}
    assert (failures(posts, "after-fix", ["invoice_finance"])
            == failures(posts, "after-release", ["invoice_finance"]))

    # The log shows which release handled each run.
    versions = log.groupby("run_label")["release_version"].unique().map(list).to_dict()
    assert versions == {"before-release": ["v1.0.0"], "after-release": ["v1.1.0"],
                        "after-fix": ["v1.1.1"]}


def test_release_detection_flags_the_right_endpoint(story):
    changes = analytics.detect_release_changes(story["log"])

    worse = changes[(changes["from_release"] == "v1.0.0") & (changes["to_release"] == "v1.1.0")]
    flagged = worse[worse["flagged"]]
    assert list(flagged["request"]) == ["POST /applications"]
    assert flagged.iloc[0]["change"] > 0.15

    better = changes[(changes["to_release"] == "v1.1.1") & (changes["request"] == "POST /applications")]
    assert better.iloc[0]["change"] < 0          # failure rate fell after the fix


def test_failures_are_concentrated_in_the_amount_band(story):
    after = story["posts"][story["posts"]["run_label"] == "after-release"]
    table, test = analytics.failure_concentration(after, "amount_band")
    assert table.iloc[0]["amount_band"] == "25k-50k"       # highest lift first
    assert table.iloc[0]["failure_rate"] == 1.0
    assert table.iloc[0]["lift"] > 2
    assert test["p_value"] < 0.001


def test_release_story_is_repeatable(client):
    first = run_release_story(client, count=60)
    client.post("/lab/reset")
    second = run_release_story(client, count=60)
    assert first == second
