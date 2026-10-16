"""The partner-facing API: success paths, validation, auth, missing resources, persistence."""

import sqlite3

import pytest

from tests.helpers import CUSTOMER, apply, new_customer


def test_health_reports_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["release_version"] == "v1.0.0"


def test_partners_lists_three_partners_without_keys(client):
    response = client.get("/partners")
    assert response.status_code == 200
    assert [p["id"] for p in response.json()["partners"]] == ["alder", "birch", "cedar"]
    assert "demo-key" not in response.text
    assert {p["product"] for p in response.json()["products"]} == {
        "term_loan", "credit_line", "invoice_finance"}


def test_openapi_docs_are_served(client):
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/health", "/customers", "/customers/{customer_id}", "/applications",
                 "/applications/{application_id}", "/partners", "/metrics"):
        assert path in paths


# --- Successful creation ----------------------------------------------------

def test_create_and_fetch_customer(client, alder):
    created = client.post("/customers", json=CUSTOMER, headers=alder)
    assert created.status_code == 201
    customer = created.json()
    assert customer["id"] == 1
    assert customer["partner_id"] == "alder"
    fetched = client.get(f"/customers/{customer['id']}", headers=alder)
    assert fetched.status_code == 200
    assert fetched.json() == customer


def test_successful_application_creation(client, alder):
    customer = new_customer(client, alder)
    response = apply(client, alder, customer["id"], product="term_loan", amount=20_000)
    assert response.status_code == 201
    application = response.json()
    assert application["customer_id"] == customer["id"]
    assert application["partner_id"] == "alder"
    assert application["amount"] == 20_000
    assert application["status"] == "approved"      # 20k is 5% of a 400k turnover
    assert application["release_version"] == "v1.0.0"

    fetched = client.get(f"/applications/{application['id']}", headers=alder)
    assert fetched.status_code == 200
    assert fetched.json() == application


@pytest.mark.parametrize("amount, trading_months, status, reason", [
    (100_000, 36, "approved", None),                              # exactly a quarter
    (120_000, 36, "referred", "amount_over_quarter_of_turnover"),
    (220_000, 36, "declined", "amount_over_half_of_turnover"),
    (20_000, 3, "declined", "trading_history_under_6_months"),
])
def test_toy_decision_outcomes(client, alder, amount, trading_months, status, reason):
    customer = new_customer(client, alder, trading_months=trading_months)
    application = apply(client, alder, customer["id"], amount=amount).json()
    assert (application["status"], application["decision_reason"]) == (status, reason)


# --- Invalid payloads -------------------------------------------------------

@pytest.mark.parametrize("change, bad_field", [
    ({"amount": None}, "amount"),                  # field removed
    ({"amount": "twenty thousand"}, "amount"),     # wrong type
    ({"amount": -5}, "amount"),                    # out of range
    ({"amount": 1500.5}, "amount"),                # pounds must be whole
    ({"product": "overdraft"}, "product"),         # not an allowed value
    ({"term_months": 0}, "term_months"),
    ({"unexpected": 1}, "unexpected"),             # unknown fields are refused
])
def test_invalid_application_payload_is_rejected(client, alder, change, bad_field):
    customer = new_customer(client, alder)
    body = {"customer_id": customer["id"], "product": "term_loan", "amount": 20_000,
            "term_months": 12, **change}
    body = {k: v for k, v in body.items() if v is not None}
    response = client.post("/applications", json=body, headers=alder)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert bad_field in [detail["field"] for detail in error["details"]]
    # Nothing was stored.
    assert client.get("/metrics").json()["applications"]["total"] == 0


def test_body_that_is_not_json_is_rejected(client, alder):
    response = client.post("/customers", content="not json",
                           headers={**alder, "Content-Type": "application/json"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_invalid_customer_email_is_rejected(client, alder):
    response = client.post("/customers", json={**CUSTOMER, "contact_email": "not-an-email"},
                           headers=alder)
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "contact_email"


def test_amount_outside_product_limits_is_rejected_by_rule_r101(client, alder):
    customer = new_customer(client, alder)
    response = apply(client, alder, customer["id"], product="credit_line", amount=150_000)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "rule_R101_product_limits"
    assert response.json()["error"]["details"] == [{"rule_id": "R-101"}]


# --- Authentication ---------------------------------------------------------

def test_missing_api_key_is_401(client):
    response = client.post("/customers", json=CUSTOMER)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "missing_api_key"


def test_unknown_api_key_is_401(client):
    response = client.get("/customers/1", headers={"X-API-Key": "not-a-real-key"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_auth_is_checked_before_the_body(client):
    """A bad key and a bad body together report the key: 401, not 422."""
    response = client.post("/applications", json={"nonsense": True})
    assert response.status_code == 401


# --- Missing resources ------------------------------------------------------

def test_missing_customer_is_404(client, alder):
    response = client.get("/customers/999", headers=alder)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "customer_not_found"


def test_missing_application_is_404(client, alder):
    response = client.get("/applications/999", headers=alder)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "application_not_found"


def test_application_for_unknown_customer_is_404(client, alder):
    response = apply(client, alder, customer_id=999)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "customer_not_found"


def test_another_partners_customer_looks_missing(client, alder, birch):
    customer = new_customer(client, alder)
    assert client.get(f"/customers/{customer['id']}", headers=birch).status_code == 404
    assert apply(client, birch, customer["id"]).status_code == 404


def test_id_that_is_not_a_number_is_422(client, alder):
    response = client.get("/applications/abc", headers=alder)
    assert response.status_code == 422
    assert response.json()["error"]["details"][0]["field"] == "path.application_id"


def test_unknown_url_is_404_route_not_found(client):
    response = client.get("/no-such-endpoint")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "route_not_found"


# --- Persistence ------------------------------------------------------------

def test_data_survives_an_api_restart(client, alder, db_file):
    from fastapi.testclient import TestClient

    import main

    customer = new_customer(client, alder)
    application = apply(client, alder, customer["id"]).json()

    # A second client starts the app again on the same database file.
    with TestClient(main.app) as restarted:
        fetched = restarted.get(f"/applications/{application['id']}", headers=alder)
        assert fetched.status_code == 200
        assert fetched.json() == application


def test_application_is_stored_in_the_sqlite_file(client, alder, db_file):
    customer = new_customer(client, alder)
    apply(client, alder, customer["id"], product="invoice_finance", amount=12_000)

    conn = sqlite3.connect(db_file)   # read the file directly, bypassing the API
    row = conn.execute("SELECT partner_id, product, amount, status FROM applications").fetchone()
    conn.close()
    assert row == ("alder", "invoice_finance", 12_000, "approved")


# --- Metrics ----------------------------------------------------------------

def test_metrics_count_requests_and_errors(client, alder):
    customer = new_customer(client, alder)                 # 201
    apply(client, alder, customer["id"])                   # 201
    client.get("/applications/999", headers=alder)         # 404
    client.post("/customers", json=CUSTOMER)               # 401, no partner

    metrics = client.get("/metrics").json()
    assert metrics["requests"]["total"] == 4
    assert metrics["requests"]["succeeded"] == 2
    assert metrics["requests"]["client_errors"] == 2
    assert metrics["requests"]["error_rate"] == 0.5
    assert metrics["by_status_code"] == {"201": 2, "401": 1, "404": 1}
    assert metrics["by_failure_reason"] == {"application_not_found": 1, "missing_api_key": 1}
    alder_row = next(p for p in metrics["by_partner"] if p["partner_id"] == "alder")
    assert (alder_row["total"], alder_row["errors"]) == (3, 1)
    assert metrics["applications"] == {"total": 1, "approved": 1, "referred": 0,
                                       "declined": 0, "approval_rate": 1.0}
