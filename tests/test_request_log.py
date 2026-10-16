"""Structured request logging: what is recorded, and what must never be."""

from datetime import datetime

from tests.helpers import CUSTOMER, apply, log_entry, new_customer


def test_request_is_logged_with_every_required_field(client, alder):
    customer = new_customer(client, alder)
    response = apply(client, alder, customer["id"], product="credit_line", amount=18_000)
    entry = log_entry(client, response)

    assert entry["request_id"] == response.headers["X-Request-ID"]
    assert datetime.fromisoformat(entry["timestamp"]).tzinfo is not None   # UTC timestamp
    assert entry["partner_id"] == "alder"
    assert entry["method"] == "POST"
    assert entry["endpoint"] == "/applications"
    assert entry["status_code"] == 201
    assert entry["latency_ms"] > 0
    assert entry["failure_reason"] is None
    assert entry["release_version"] == "v1.0.0"
    assert entry["metadata"]["product"] == "credit_line"
    assert entry["metadata"]["amount"] == 18_000
    assert entry["metadata"]["application_id"] == response.json()["id"]
    assert entry["metadata"]["decision"] == "approved"


def test_endpoint_is_the_route_template_and_path_is_the_real_url(client, alder):
    response = client.get("/applications/999", headers=alder)
    entry = log_entry(client, response)
    assert entry["endpoint"] == "/applications/{application_id}"
    assert entry["path"] == "/applications/999"


def test_failure_reasons_are_recorded(client, alder):
    customer = new_customer(client, alder)
    cases = [
        (client.post("/customers", json=CUSTOMER), 401, "missing_api_key"),
        (client.get("/customers/999", headers=alder), 404, "customer_not_found"),
        (apply(client, alder, customer["id"], amount=-1), 422, "validation_error"),
        (apply(client, alder, customer["id"], amount=900_000), 422, "rule_R101_product_limits"),
        (client.get("/nowhere"), 404, "route_not_found"),
    ]
    for response, status_code, reason in cases:
        entry = log_entry(client, response)
        assert (entry["status_code"], entry["failure_reason"]) == (status_code, reason)


def test_error_body_and_header_carry_the_same_request_id(client):
    response = client.get("/customers/1")
    assert response.json()["error"]["request_id"] == response.headers["X-Request-ID"]


def test_validation_failures_record_field_names_but_not_values(client, alder):
    customer = new_customer(client, alder)
    response = client.post("/applications", headers=alder, json={
        "customer_id": customer["id"], "product": "term_loan",
        "amount": "sensitive-looking-text", "term_months": 12})
    entry = log_entry(client, response)
    assert entry["metadata"]["invalid_fields"] == ["amount"]
    assert "sensitive-looking-text" not in str(entry)
    assert "sensitive-looking-text" not in response.text


def test_unknown_request_id_is_404(client):
    response = client.get("/lab/requests/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "request_not_found"


def test_api_keys_and_emails_are_never_written_to_the_log(client, alder, db_file):
    customer = new_customer(client, alder)
    apply(client, alder, customer["id"])
    client.get("/customers/1", headers={"X-API-Key": "wrong-key-that-looks-secret"})

    rows = client.get("/lab/requests", params={"limit": 100}).json()
    assert len(rows) == 3
    everything_logged = str(rows)
    assert "demo-key-alder-0001" not in everything_logged
    assert "wrong-key-that-looks-secret" not in everything_logged
    assert CUSTOMER["contact_email"] not in everything_logged

    # Belt and braces: the keys are not anywhere in the database files either.
    for path in db_file.parent.glob("lab.db*"):
        raw = path.read_bytes()
        assert b"demo-key-alder-0001" not in raw
        assert b"wrong-key-that-looks-secret" not in raw


def test_operational_and_lab_calls_are_not_logged(client):
    client.get("/health")
    client.get("/metrics")
    client.get("/lab/config")
    client.get("/docs")
    assert client.get("/lab/requests").json() == []
    client.get("/partners")
    assert len(client.get("/lab/requests").json()) == 1


def test_run_label_and_journey_number_are_recorded(client, alder):
    headers = {**alder, "X-Lab-Run": "before-release", "X-Lab-Journey": "17"}
    entry = log_entry(client, client.post("/customers", json=CUSTOMER, headers=headers))
    assert entry["run_label"] == "before-release"
    assert entry["metadata"]["journey"] == 17


def test_run_label_is_cleaned(client, alder):
    headers = {**alder, "X-Lab-Run": "<script>alert(1)</script>" + "x" * 80}
    entry = log_entry(client, client.post("/customers", json=CUSTOMER, headers=headers))
    assert "<" not in entry["run_label"]
    assert len(entry["run_label"]) <= 40


def test_request_list_filters(client, alder, birch):
    new_customer(client, alder)
    new_customer(client, birch)
    client.get("/customers/999", headers=birch)

    def listed(**params):
        return client.get("/lab/requests", params=params).json()

    assert len(listed()) == 3
    assert [r["id"] for r in listed()] == [3, 2, 1]             # newest first
    assert len(listed(partner_id="birch")) == 2
    assert len(listed(failures_only=True)) == 1
    assert len(listed(failure_reason="customer_not_found")) == 1
    assert len(listed(limit=1)) == 1
