"""Feature flags and fault injection: each fault must change real API behaviour."""

from partnerflow import faults
from tests.helpers import CUSTOMER, apply, log_entry, new_customer


def set_fault(client, name, **settings):
    response = client.put(f"/lab/faults/{name}", json=settings)
    assert response.status_code == 200, response.text
    return response.json()


# --- Feature flags ----------------------------------------------------------

def test_default_configuration(client):
    lab = client.get("/lab/config").json()
    assert lab["release"] == "A"
    assert lab["hotfix"] is False
    assert lab["version"] == "v1.0.0"
    assert lab["r204_band"] == [25_000, 50_000]
    assert all(not fault["enabled"] for fault in lab["faults"].values())


def test_flags_are_stored_in_sqlite_and_survive_a_restart(client):
    from fastapi.testclient import TestClient

    import main

    client.post("/lab/release", json={"release": "B"})
    set_fault(client, "latency", enabled=True, delay_ms=40, partner="alder")

    with TestClient(main.app) as restarted:
        lab = restarted.get("/lab/config").json()
        assert lab["release"] == "B"
        assert lab["version"] == "v1.1.0"
        assert lab["faults"]["latency"] == {"enabled": True, "partner": "alder", "delay_ms": 40}


def test_flag_change_takes_effect_on_the_next_request(client, alder):
    customer = new_customer(client, alder)
    assert apply(client, alder, customer["id"], amount=30_000).status_code == 201
    client.post("/lab/release", json={"release": "B"})
    assert apply(client, alder, customer["id"], amount=30_000).status_code == 422
    client.post("/lab/release", json={"release": "A"})
    assert apply(client, alder, customer["id"], amount=30_000).status_code == 201


def test_unknown_fault_and_unsupported_setting(client):
    assert client.put("/lab/faults/gremlins", json={"enabled": True}).status_code == 404
    response = client.put("/lab/faults/latency", json={"every_nth": 2})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "setting_not_supported"


def test_hotfix_needs_release_b(client):
    response = client.post("/lab/hotfix", json={"enabled": True})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "hotfix_requires_release_b"


def test_activating_a_release_removes_the_hotfix(client):
    client.post("/lab/release", json={"release": "B"})
    assert client.post("/lab/hotfix", json={"enabled": True}).json()["version"] == "v1.1.1"
    assert client.post("/lab/release", json={"release": "B"}).json()["version"] == "v1.1.0"


# --- A. Authentication failure ----------------------------------------------

def test_auth_revoked_fault_blocks_only_the_chosen_partner(client, alder, cedar):
    new_customer(client, cedar)   # works before the fault
    set_fault(client, "auth_revoked", enabled=True, partner="cedar")

    blocked = client.get("/customers/1", headers=cedar)
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "api_key_revoked"
    entry = log_entry(client, blocked)
    assert (entry["partner_id"], entry["failure_reason"]) == ("cedar", "api_key_revoked")

    assert client.post("/customers", json=CUSTOMER, headers=alder).status_code == 201

    set_fault(client, "auth_revoked", enabled=False)
    assert client.get("/customers/1", headers=cedar).status_code == 200


# --- D. Server-side exception -----------------------------------------------

def test_server_error_fault_raises_a_real_exception_on_every_nth_call(client, alder):
    customer = new_customer(client, alder)
    set_fault(client, "server_error", enabled=True, partner="all", every_nth=2)

    responses = [apply(client, alder, customer["id"]) for _ in range(4)]
    assert [r.status_code for r in responses] == [201, 500, 201, 500]

    failed = responses[1]
    assert failed.json()["error"]["code"] == "server_exception"
    entry = log_entry(client, failed)
    assert entry["failure_reason"] == "server_exception"
    assert entry["metadata"]["exception_type"] == "ScoringServiceError"
    assert "call_scoring_service" in entry["metadata"]["raised_in"]
    # The crashed requests stored nothing.
    assert client.get("/metrics").json()["applications"]["total"] == 2
    assert client.get("/metrics").json()["requests"]["server_errors"] == 2


# --- E. Increased latency ---------------------------------------------------

def test_latency_fault_slows_the_chosen_partner(client, alder, birch):
    set_fault(client, "latency", enabled=True, partner="birch", delay_ms=120)

    slow = log_entry(client, client.post("/customers", json=CUSTOMER, headers=birch))
    fast = log_entry(client, client.post("/customers", json=CUSTOMER, headers=alder))

    assert slow["status_code"] == 201            # slower, but still successful
    assert slow["latency_ms"] >= 120
    assert slow["metadata"]["injected_delay_ms"] == 120
    assert fast["latency_ms"] < 120
    assert "injected_delay_ms" not in fast["metadata"]


# --- F. Rate limiting -------------------------------------------------------

def test_rate_limit_fault_returns_429_after_the_limit(client, alder, birch):
    set_fault(client, "rate_limit", enabled=True, partner="alder", limit=3, window_seconds=60)

    codes = [client.post("/customers", json=CUSTOMER, headers=alder).status_code for _ in range(5)]
    assert codes == [201, 201, 201, 429, 429]

    limited = client.post("/customers", json=CUSTOMER, headers=alder)
    assert limited.json()["error"]["code"] == "rate_limited"
    assert 1 <= int(limited.headers["Retry-After"]) <= 60
    assert log_entry(client, limited)["failure_reason"] == "rate_limited"

    # Another partner is not affected.
    assert client.post("/customers", json=CUSTOMER, headers=birch).status_code == 201


def test_rate_limit_window_expires(client, alder, monkeypatch):
    set_fault(client, "rate_limit", enabled=True, partner="alder", limit=1, window_seconds=30)
    clock = {"now": 1000.0}
    monkeypatch.setattr(faults.time, "monotonic", lambda: clock["now"])

    assert client.post("/customers", json=CUSTOMER, headers=alder).status_code == 201
    assert client.post("/customers", json=CUSTOMER, headers=alder).status_code == 429
    clock["now"] += 31   # the window has passed
    assert client.post("/customers", json=CUSTOMER, headers=alder).status_code == 201


# --- H. Webhook delivery failure --------------------------------------------

def test_webhook_failure_is_recorded_and_can_be_redelivered(client, alder, cedar):
    cedar_customer = new_customer(client, cedar)
    alder_customer = new_customer(client, alder)
    set_fault(client, "webhook_failure", enabled=True, partner="cedar")

    response = apply(client, cedar, cedar_customer["id"])
    assert response.status_code == 201          # the application itself still succeeds
    assert log_entry(client, response)["metadata"]["webhook"] == "failed"
    assert apply(client, alder, alder_customer["id"]).status_code == 201

    failed = client.get("/lab/webhooks", params={"failed_only": True}).json()
    assert len(failed) == 1
    assert failed[0]["partner_id"] == "cedar"
    assert failed[0]["attempts"] == faults.WEBHOOK_MAX_ATTEMPTS
    assert failed[0]["last_status_code"] == 503

    # Redelivering while the receiver is still down changes nothing.
    assert client.post("/lab/webhooks/redeliver").json() == {
        "retried": 1, "recovered": 0, "still_failed": 1}

    set_fault(client, "webhook_failure", enabled=False)
    assert client.post("/lab/webhooks/redeliver").json() == {
        "retried": 1, "recovered": 1, "still_failed": 0}
    assert client.get("/lab/webhooks", params={"failed_only": True}).json() == []
    assert len(client.get("/lab/webhooks").json()) == 2


# --- Reset ------------------------------------------------------------------

def test_reset_clears_data_and_restores_defaults(client, alder):
    new_customer(client, alder)
    client.post("/lab/release", json={"release": "B"})
    set_fault(client, "latency", enabled=True, delay_ms=10)

    lab = client.post("/lab/reset").json()
    assert lab["release"] == "A"
    assert lab["faults"]["latency"]["enabled"] is False
    assert client.get("/lab/requests").json() == []
    assert client.get("/customers/1", headers=alder).status_code == 404
    assert new_customer(client, alder)["id"] == 1     # IDs start again from 1


def test_reset_can_keep_the_configuration(client, alder):
    new_customer(client, alder)
    client.post("/lab/release", json={"release": "B"})
    lab = client.post("/lab/reset", json={"keep_config": True}).json()
    assert lab["release"] == "B"
    assert client.get("/metrics").json()["requests"]["total"] == 0
