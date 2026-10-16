"""The traffic generator: reproducible, and only ever talks to the API over HTTP."""

from partnerflow import analytics, traffic


def set_fault(client, name, **settings):
    assert client.put(f"/lab/faults/{name}", json=settings).status_code == 200


def test_same_seed_gives_identical_journeys():
    assert traffic.build_journeys(seed=7, count=50) == traffic.build_journeys(seed=7, count=50)
    assert traffic.build_journeys(seed=7, count=50) != traffic.build_journeys(seed=8, count=50)


def test_a_longer_run_starts_with_the_same_journeys():
    assert traffic.build_journeys(7, 80)[:50] == traffic.build_journeys(7, 50)


def test_journeys_cover_every_partner_and_product():
    journeys = traffic.build_journeys(seed=7, count=150)
    assert {j["partner_id"] for j in journeys} == {"alder", "birch", "cedar"}
    assert {j["application"]["product"] for j in journeys} == {
        "term_loan", "credit_line", "invoice_finance"}
    assert all(j["application"]["amount"] % 500 == 0 for j in journeys)


def test_every_generated_request_is_a_real_logged_api_call(client, db_file):
    summary = traffic.generate("healthy", count=40, seed=7, client=client)
    log = analytics.load_requests(db_file)

    assert len(log) == summary["requests"]            # nothing bypassed the API
    assert (log["run_label"] == "healthy").all()
    assert summary["failures"] == int(log["failed"].sum())
    assert summary["applications_sent"] == 40
    assert set(log["journey"].dropna().astype(int)) == set(range(40))
    # Healthy traffic only fails on the intended product limits.
    assert set(log["failure_reason"].dropna()) <= {"rule_R101_product_limits"}


def test_bad_payload_fault_makes_that_partner_send_invalid_bodies(client, db_file):
    set_fault(client, "bad_payloads", enabled=True, partner="birch", share=1.0)
    traffic.generate("bad-payloads", count=40, seed=7, client=client)
    posts = analytics.load_requests(db_file).query("request == 'POST /applications'")

    birch = posts[posts["partner_id"] == "birch"]
    assert len(birch) > 0
    assert (birch["status_code"] == 422).all()
    assert (birch["failure_reason"] == "validation_error").all()
    others = posts[posts["partner_id"] != "birch"]
    assert "validation_error" not in set(others["failure_reason"].dropna())


def test_missing_resources_fault_makes_that_partner_use_unknown_ids(client, db_file):
    set_fault(client, "missing_resources", enabled=True, partner="cedar", share=1.0)
    traffic.generate("missing", count=60, seed=7, client=client)
    log = analytics.load_requests(db_file)

    cedar_reasons = set(log[log["partner_id"] == "cedar"]["failure_reason"].dropna())
    assert "customer_not_found" in cedar_reasons
    assert "application_not_found" in cedar_reasons
    assert (log[log["failure_reason"].isin(["customer_not_found", "application_not_found"])]
            ["partner_id"] == "cedar").all()
    assert set(log[log["failure_reason"] == "customer_not_found"]["status_code"]) == {404}


def test_client_faults_do_not_change_the_underlying_journeys(client, db_file):
    """With a client fault on, unaffected partners send exactly what they sent before."""
    healthy = traffic.generate("healthy", count=40, seed=7, client=client)
    set_fault(client, "bad_payloads", enabled=True, partner="birch", share=0.5)
    faulty = traffic.generate("faulty", count=40, seed=7, client=client)
    log = analytics.load_requests(db_file)

    def alder_amounts(run_label):
        rows = log[(log["run_label"] == run_label) & (log["partner_id"] == "alder")
                   & (log["request"] == "POST /applications")]
        return rows["amount"].tolist()

    assert alder_amounts("healthy") == alder_amounts("faulty")
    assert faulty["failures"] > healthy["failures"]
