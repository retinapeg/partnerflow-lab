"""The triage agent, driven through the API: decide, act within the runbook, log."""

import json

import httpx
import pytest

from partnerflow import traffic, triage

WEBHOOK_FP = "cedar · 503 receiver_unavailable"
AUTH_FP = "cedar · 401 api_key_revoked"


def recurring_story(client) -> None:
    """Healthy traffic, then cedar's key is revoked twice with a healthy spell between."""
    client.post("/lab/reset")
    traffic.generate("healthy-1", 60, 7, client)
    client.put("/lab/faults/auth_revoked", json={"enabled": True, "partner": "cedar"})
    traffic.generate("incident-1", 40, 8, client)
    client.put("/lab/faults/auth_revoked", json={"enabled": False})
    traffic.generate("healthy-2", 80, 9, client)
    client.put("/lab/faults/auth_revoked", json={"enabled": True, "partner": "cedar"})
    traffic.generate("incident-2", 40, 10, client)
    client.put("/lab/faults/auth_revoked", json={"enabled": False})


def test_recurring_endpoint_reports_a_recurrence_with_its_statistics(client):
    recurring_story(client)
    data = client.get("/lab/recurring").json()
    assert data["parameters"]["clean_requests"] == 10
    row = next(r for r in data["registry"] if r["fingerprint"] == AUTH_FP)
    assert row["episodes"] == 2 and row["recurring"] and row["open"]
    assert row["flagged"] and row["status"] == "active"
    assert row["owner"] == "us" and row["class"] == "authentication"
    assert row["baseline_failures"] == 0 and row["baseline_clean_requests"] > 0
    assert row["tail_p"] < 1e-6
    assert row["posterior_low"] <= row["posterior_mean"] <= row["posterior_high"] < 1.0
    episodes = [e for e in data["episodes"] if e["fingerprint"] == AUTH_FP]
    assert [e["episode"] for e in episodes] == [1, 2]
    assert all(e["flagged"] for e in episodes)
    # Everything the API returns is JSON the dashboard can show.
    json.dumps(data)


def test_triage_decides_acts_within_the_runbook_and_logs(client):
    recurring_story(client)
    result = client.post("/lab/triage").json()
    assert result["mode"] == "rules" and not result["dry_run"]
    entry = next(t for t in result["triaged"] if t["fingerprint"] == AUTH_FP)
    decision = entry["decision"]
    assert decision["status"] == "act" and decision["owner"] == "us" and decision["severity"] == "high"
    assert decision["runbook"] == "api_key_revoked"
    assert "Cedar Pay" in decision["partner_message"] and "401" in decision["partner_message"]
    assert "second time" in decision["partner_message"], "a recurrence asks for a permanent fix"
    assert "tail probability" in decision["summary"]
    assert entry["actions"] == [{"action": "open_incident", "result": "simulated",
                                 "description": "Open an incident record so the pattern is tracked to resolution (simulated)."}]
    assert entry["evidence"]["latest"]["flagged"] and entry["evidence"]["recurring"]
    assert entry["evidence"]["other_partners_same_failure"] == []

    log = client.get("/lab/triage").json()
    assert log["auto_triage"]["enabled"] is False
    logged = next(e for e in log["entries"] if e["fingerprint"] == AUTH_FP)
    assert logged["status"] == "act" and logged["episode"] == 2 and logged["dry_run"] is False
    assert logged["decision"]["partner_message"] == decision["partner_message"]


def test_a_second_pass_is_idempotent_until_the_episode_grows(client):
    recurring_story(client)
    first = client.post("/lab/triage").json()
    assert any(t["fingerprint"] == AUTH_FP for t in first["triaged"])
    second = client.post("/lab/triage").json()
    assert not any(t["fingerprint"] == AUTH_FP for t in second["triaged"])
    assert any(s["fingerprint"] == AUTH_FP and "already acted" in s["why"] for s in second["skipped"])
    # The same episode continuing: still nothing new to do.
    client.put("/lab/faults/auth_revoked", json={"enabled": True, "partner": "cedar"})
    traffic.generate("incident-2b", 20, 11, client)
    third = client.post("/lab/triage").json()
    assert not any(t["fingerprint"] == AUTH_FP for t in third["triaged"])
    # A new episode after a healthy spell: the agent acts again.
    client.put("/lab/faults/auth_revoked", json={"enabled": False})
    traffic.generate("healthy-3", 80, 12, client)
    client.put("/lab/faults/auth_revoked", json={"enabled": True, "partner": "cedar"})
    traffic.generate("incident-3", 40, 13, client)
    fourth = client.post("/lab/triage").json()
    entry = next(t for t in fourth["triaged"] if t["fingerprint"] == AUTH_FP)
    assert entry["episode"] == 3 and "third time" in entry["decision"]["partner_message"]


def test_a_watch_decision_is_logged_once_and_revisited_when_the_episode_grows(client):
    client.post("/lab/reset")
    traffic.generate("healthy", 60, 7, client)
    cedar = {"X-API-Key": "demo-key-cedar-0003"}
    # Two unknown-customer applications: an anecdote, not a pattern.
    for index in range(2):
        client.post("/applications", headers=cedar,
                    json={"customer_id": 90_000_000 + index, "product": "term_loan", "amount": 20_000, "term_months": 12})
    fp = "cedar · 404 customer_not_found"
    first = client.post("/lab/triage").json()
    entry = next(t for t in first["triaged"] if t["fingerprint"] == fp)
    assert entry["decision"]["status"] == "watch" and entry["actions"] == []
    assert entry["decision"]["partner_message"] == ""
    second = client.post("/lab/triage").json()
    assert any(s["fingerprint"] == fp and "unchanged" in s["why"] for s in second["skipped"])
    for index in range(2, 6):
        client.post("/applications", headers=cedar,
                    json={"customer_id": 90_000_000 + index, "product": "term_loan", "amount": 20_000, "term_months": 12})
    third = client.post("/lab/triage").json()
    entry = next(t for t in third["triaged"] if t["fingerprint"] == fp)
    assert entry["decision"]["status"] == "act" and entry["decision"]["owner"] == "partner"


def test_dry_run_decides_but_never_acts(client):
    client.post("/lab/reset")
    traffic.generate("healthy", 60, 7, client)
    client.put("/lab/faults/webhook_failure", json={"enabled": True, "partner": "cedar"})
    traffic.generate("incident", 40, 8, client)
    result = client.post("/lab/triage", json={"dry_run": True}).json()
    entry = next(t for t in result["triaged"] if t["fingerprint"] == WEBHOOK_FP)
    assert entry["actions"][0]["result"].startswith("dry run")
    failed_before = len(client.get("/lab/webhooks", params={"failed_only": True, "limit": 1000}).json())
    assert failed_before > 0
    # A dry run does not count as handled: the real pass still acts.
    real = client.post("/lab/triage").json()
    entry = next(t for t in real["triaged"] if t["fingerprint"] == WEBHOOK_FP)
    assert entry["actions"][0]["action"] == "redeliver_webhooks"
    assert entry["actions"][0]["result"] == "executed" and entry["actions"][0]["recovered"] == 0


def test_webhook_replay_keeps_retrying_until_the_receiver_recovers(client):
    client.post("/lab/reset")
    traffic.generate("healthy", 60, 7, client)
    client.put("/lab/faults/webhook_failure", json={"enabled": True, "partner": "cedar"})
    traffic.generate("incident", 40, 8, client)
    client.post("/lab/triage")                                       # acts: replay fails, receiver down
    still = client.post("/lab/triage").json()
    assert still["followups"][0]["result"] == "retried, receiver still failing"
    client.put("/lab/faults/webhook_failure", json={"enabled": False})
    recovered = client.post("/lab/triage").json()
    followup = next(f for f in recovered["followups"] if f.get("fingerprint") == WEBHOOK_FP)
    assert followup["status"] == "followup" and followup["actions"][0]["recovered"] > 0
    assert client.get("/lab/webhooks", params={"failed_only": True}).json() == []
    assert client.post("/lab/triage").json()["followups"] == [], "nothing left to replay"


def test_server_errors_are_always_ours_and_all_partners_widen_the_blast_radius(client):
    client.post("/lab/reset")
    traffic.generate("healthy", 90, 7, client)
    client.put("/lab/faults/server_error", json={"enabled": True, "partner": "all", "every_nth": 2})
    traffic.generate("incident", 90, 8, client)
    result = client.post("/lab/triage").json()
    entries = [t for t in result["triaged"] if t["failure_reason"] == "server_exception"]
    assert len(entries) == 3
    for entry in entries:
        assert entry["decision"]["owner"] == "us" and entry["decision"]["status"] == "act"
        assert entry["decision"]["blast_radius"] == "all partners"
        assert entry["actions"][0]["action"] == "page_oncall"
        assert entry["evidence"]["exception_types"][0]["exception"] == "ScoringServiceError"


def test_guardrails_override_a_bad_decision():
    evidence = {"status_code": 500, "failure_reason": "server_exception", "runbook": "server_exception",
                "latest": {"flagged": True}}
    decision = triage.guardrails({"status": "act", "owner": "partner", "auto_action": "redeliver_webhooks"}, evidence)
    assert decision["owner"] == "us" and decision["auto_action"] is None
    assert len(decision["guardrail_notes"]) == 2
    evidence["latest"]["flagged"] = False
    decision = triage.guardrails({"status": "act", "owner": "us", "auto_action": "page_oncall"}, evidence)
    assert decision["status"] == "watch" and decision["auto_action"] is None


def test_llm_mode_falls_back_to_the_rules_without_a_key(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    recurring_story(client)
    result = client.post("/lab/triage", json={"mode": "llm"}).json()
    assert result["llm_available"] is False
    entry = next(t for t in result["triaged"] if t["fingerprint"] == AUTH_FP)
    assert entry["decision"]["mode"] == "rules" and "ANTHROPIC_API_KEY" in entry["decision"]["llm_note"]


def test_llm_mode_uses_the_models_answer_inside_the_guardrails(client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-not-real")
    answer = {"status": "act", "class": "authentication", "owner": "partner", "severity": "medium",
              "blast_radius": "one partner", "summary": "Model summary.", "diagnosis": "Model diagnosis.",
              "checks": ["check one"], "internal_action": "Model action.", "auto_action": "page_oncall",
              "partner_message": "Model message.", "confidence": "high"}

    def fake_post(url, json=None, headers=None, timeout=None):
        assert url == triage.ANTHROPIC_URL and headers["x-api-key"] == "test-key-not-real"
        assert json["model"] and json["messages"][0]["role"] == "user"
        request = httpx.Request("POST", url)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "```json\n" + __import__("json").dumps(answer) + "\n```"}]},
                              request=request)

    monkeypatch.setattr(triage.httpx, "post", fake_post)
    recurring_story(client)
    result = client.post("/lab/triage", json={"mode": "llm"}).json()
    entry = next(t for t in result["triaged"] if t["fingerprint"] == AUTH_FP)
    decision = entry["decision"]
    assert decision["mode"] == "llm" and decision["summary"] == "Model summary."
    assert decision["partner_message"] == "Model message."
    # The model wanted to page on-call for a key problem: not in this runbook, so removed.
    assert decision["auto_action"] is None and entry["actions"] == []
    assert any("not allowed by the runbook" in note for note in decision["guardrail_notes"])


def test_auto_triage_setting_round_trips(client):
    state = client.put("/lab/triage/auto", json={"enabled": True, "interval_seconds": 15, "mode": "rules"}).json()
    assert state["auto_triage"] == {"enabled": True, "interval_seconds": 15, "mode": "rules"}
    assert client.get("/lab/triage").json()["auto_triage"]["enabled"] is True
    state = client.put("/lab/triage/auto", json={"enabled": False}).json()
    assert state["auto_triage"]["enabled"] is False and state["auto_triage"]["interval_seconds"] == 15
    assert client.put("/lab/triage/auto", json={"interval_seconds": 1}).status_code == 422


def test_reset_clears_the_triage_log(client):
    recurring_story(client)
    client.post("/lab/triage")
    assert client.get("/lab/triage").json()["entries"]
    client.post("/lab/reset")
    assert client.get("/lab/triage").json()["entries"] == []
    assert client.get("/lab/recurring").json()["registry"] == []
