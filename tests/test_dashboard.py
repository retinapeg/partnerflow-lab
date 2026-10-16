"""The dashboard, driven end to end against a real API server.

These tests start Uvicorn on a spare port with a temporary database, then run
the Streamlit app headlessly (Streamlit's AppTest) and click its widgets.
They prove the dashboard changes the API's real behaviour, not just its own display.
"""

import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from partnerflow import config

PAGES = ["Overview", "API playground", "Fault injection", "Release simulation",
         "Incident investigation", "Data science", "Broker monitoring"]
CEDAR = {"X-API-Key": "demo-key-cedar-0003"}
CUSTOMER = {"business_name": "Canal Print Ltd", "contact_email": "owner@canalprint.example",
            "annual_turnover": 150_000, "trading_months": 40}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    """A real API server process with its own empty database."""
    db_file = tmp_path_factory.mktemp("dashboard") / "lab.db"
    url = f"http://127.0.0.1:{free_port()}"
    env = {**os.environ, "PARTNERFLOW_DB": str(db_file), "PARTNERFLOW_API_URL": url}
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1",
         "--port", url.rsplit(":", 1)[1], "--log-level", "warning"],
        cwd=config.PROJECT_DIR, env=env)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.1)
        else:
            raise RuntimeError("The API server did not start.")
        # The dashboard runs inside this test process, so it reads these too.
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("PARTNERFLOW_DB", str(db_file))
            patch.setenv("PARTNERFLOW_API_URL", url)
            yield url
    finally:
        server.terminate()
        server.wait(timeout=10)


def open_page(page: str) -> AppTest:
    app = AppTest.from_file(str(config.PROJECT_DIR / "dashboard.py"), default_timeout=60)
    app.session_state["page"] = page
    app.run()
    assert not app.exception, [str(e.value) for e in app.exception]
    return app


def click(app: AppTest, label: str) -> AppTest:
    button = next(b for b in app.button if b.label == label)
    button.click().run()
    assert not app.exception, [str(e.value) for e in app.exception]
    return app


def text_of(app: AppTest) -> str:
    parts = [e.value for group in (app.markdown, app.success, app.error, app.info, app.warning,
                                   app.caption) for e in group]
    return "\n".join(str(part) for part in parts)


def test_every_page_renders_on_an_empty_lab_with_the_banner(api):
    httpx.post(f"{api}/lab/reset")
    for page in PAGES:
        app = open_page(page)
        assert app.warning[0].value == config.BANNER
        assert page in [title.value for title in app.title]


def test_dashboard_shows_a_clear_message_when_the_api_is_down(monkeypatch):
    monkeypatch.setenv("PARTNERFLOW_API_URL", f"http://127.0.0.1:{free_port()}")
    app = AppTest.from_file(str(config.PROJECT_DIR / "dashboard.py"), default_timeout=30).run()
    assert not app.exception
    assert "The API is not answering" in app.error[0].value


def test_playground_submits_a_real_application(api):
    httpx.post(f"{api}/lab/reset")
    app = open_page("API playground")
    click(app, "Send request")                                   # POST /customers
    assert app.session_state["playground_result"]["status_code"] == 201

    app.selectbox[1].set_value("POST /applications").run()
    click(app, "Send request")
    result = app.session_state["playground_result"]
    assert result["status_code"] == 201
    assert result["body"]["status"] == "approved"
    # The application really exists in the API, and the request really was logged.
    stored = httpx.get(f"{api}/applications/{result['body']['id']}",
                       headers={"X-API-Key": "demo-key-alder-0001"})
    assert stored.status_code == 200
    assert httpx.get(f"{api}/lab/requests/{result['request_id']}").json()["status_code"] == 201


def test_playground_explains_a_failed_request(api):
    httpx.post(f"{api}/lab/reset")
    app = open_page("API playground")
    app.selectbox[0].set_value("Nobody (no API key)").run()
    click(app, "Send request")
    assert app.session_state["playground_result"]["status_code"] == 401
    assert "missing_api_key" in text_of(app)
    assert "require_partner()" in text_of(app)


def test_fault_switched_on_in_the_dashboard_changes_real_api_behaviour(api):
    httpx.post(f"{api}/lab/reset")
    assert httpx.post(f"{api}/customers", json=CUSTOMER, headers=CEDAR).status_code == 201

    app = open_page("Fault injection")
    app.toggle(key="auth_revoked_enabled").set_value(True).run()
    assert not app.exception

    blocked = httpx.post(f"{api}/customers", json=CUSTOMER, headers=CEDAR)
    assert blocked.status_code == 401
    assert blocked.json()["error"]["code"] == "api_key_revoked"
    assert "A. Authentication failure" in text_of(app)           # shown in the sidebar

    app.toggle(key="auth_revoked_enabled").set_value(False).run()
    assert httpx.post(f"{api}/customers", json=CUSTOMER, headers=CEDAR).status_code == 201


def test_fault_settings_changed_in_the_dashboard_reach_the_api(api):
    httpx.post(f"{api}/lab/reset")
    app = open_page("Fault injection")
    app.slider(key="latency_delay_ms").set_value(400).run()
    app.selectbox(key="latency_partner").set_value("cedar").run()
    app.toggle(key="latency_enabled").set_value(True).run()
    assert httpx.get(f"{api}/lab/config").json()["faults"]["latency"] == {
        "enabled": True, "partner": "cedar", "delay_ms": 400}

    response = httpx.post(f"{api}/customers", json=CUSTOMER, headers=CEDAR)
    assert response.status_code == 201
    logged = httpx.get(f"{api}/lab/requests/{response.headers['X-Request-ID']}").json()
    assert logged["latency_ms"] >= 400


def test_release_story_end_to_end_through_the_dashboard(api):
    httpx.post(f"{api}/lab/reset")
    app = open_page("Release simulation")

    click(app, "Generate 'before-release' traffic")
    click(app, "Activate release v1.1.0")
    assert httpx.get(f"{api}/health").json()["release_version"] == "v1.1.0"
    click(app, "Generate 'after-release' traffic")

    text = text_of(app)
    assert "The refusal rate for POST /applications rose by" in text
    assert "it also refusing **credit_line, term_loan**" in text

    click(app, "Apply patch v1.1.1")
    assert httpx.get(f"{api}/health").json()["release_version"] == "v1.1.1"
    click(app, "Generate 'after-fix' traffic")

    text = text_of(app)
    assert "Restored." in text
    assert "The intended rule is still enforced" in text

    # The numbers on the page come from the API's own log.
    metrics = httpx.get(f"{api}/metrics", params={"run_label": "after-release"}).json()
    assert metrics["by_failure_reason"]["rule_R204_amount_band"] > 20
    fixed = httpx.get(f"{api}/metrics", params={"run_label": "after-fix"}).json()
    assert 0 < fixed["by_failure_reason"]["rule_R204_amount_band"] < metrics[
        "by_failure_reason"]["rule_R204_amount_band"]


def test_pages_render_with_recorded_data(api):
    """Runs after the release story, so the log has three releases in it."""
    for page in PAGES:
        open_page(page)
    investigation = open_page("Incident investigation")
    assert "What changed between releases?" in "\n".join(h.value for h in investigation.subheader)


def test_fault_page_probe_and_traffic_run_show_the_fault(api):
    httpx.post(f"{api}/lab/reset")
    app = open_page("Fault injection")
    app.toggle(key="server_error_enabled").set_value(True).run()
    app.number_input(key="server_error_every_nth").set_value(1).run()

    app.selectbox(key="probe_partner").set_value("birch").run()
    click(app, "Send POST /customers")                 # customers are not scored: still fine
    assert app.session_state["probe_result"]["status_code"] == 201

    click(app, "Generate traffic")
    summary = app.session_state["fault_run"]
    # Every application either hit a product limit (422) or crashed in scoring (500).
    codes = summary["by_status_code"]
    assert summary["applications_accepted"] == 0
    assert codes["500"] + codes.get("422", 0) == summary["applications_sent"]
    assert codes["500"] > 50
    assert "server_exception" in str(app.dataframe[1].value)   # the reasons table
    log = httpx.get(f"{api}/lab/requests", params={"failure_reason": "server_exception",
                                                   "limit": 1}).json()
    assert log[0]["metadata"]["exception_type"] == "ScoringServiceError"


def test_investigation_page_traces_a_request_by_id(api):
    httpx.post(f"{api}/lab/reset")
    failed = httpx.get(f"{api}/applications/424242", headers=CEDAR)
    request_id = failed.headers["X-Request-ID"]

    app = open_page("Incident investigation")
    app.text_input[0].set_value(request_id).run()
    assert not app.exception
    text = text_of(app)
    assert "application_not_found" in text
    assert "get_application() in main.py" in text      # where the error came from
    assert request_id in str(app.json[0].value)

    app.text_input[0].set_value("no-such-id").run()
    assert "No recorded request has the ID" in text_of(app)


def test_data_science_page_recomputes_when_the_inputs_change(api):
    app = open_page("Data science")

    def tiles() -> dict:
        return {metric.label: metric.value for metric in app.metric}

    default = tiles()
    assert default["Sample size"] == "12,000"
    assert default["Control conversion"] == "67.0%"
    assert default["Absolute treatment effect"] == "+5.5 pts"

    app.select_slider[0].set_value(1000).run()         # a much smaller sample
    small = tiles()
    assert small["Sample size"] == "1,000"
    assert small["95% confidence interval"] != default["95% confidence interval"]

    outcome = next(radio for radio in app.radio if radio.label == "Outcome to measure")
    outcome.set_value("funded").run()
    assert not app.exception
    assert tiles()["Control conversion"] != small["Control conversion"]


def test_broker_monitoring_page_detects_the_built_in_incident(api):
    app = open_page("Broker monitoring")
    headers = [h.value for h in app.header]
    assert any(h.startswith("4. Investigation: Meridian Capital") for h in headers)
    assert "7. The maths on this page" in headers
    assert "flags are expected" in text_of(app)

    tiles = {metric.label: metric.value for metric in app.metric}
    assert tiles["Rows stored"] == "1,680"
    assert int(tiles["Requests represented"].replace(",", "")) > 950_000
    assert tiles["Peak z-score"].endswith("σ")

    # Switching the built-in incident off leaves only chance flags, if any.
    built_in = next(t for t in app.toggle if t.label == "Built-in incident")
    built_in.set_value(False).run()
    assert not app.exception
    assert not any("Meridian Capital, Wed 07 Oct 09:00" in h.value for h in app.header)


def test_reset_button_clears_the_lab(api):
    assert httpx.get(f"{api}/metrics").json()["requests"]["total"] > 0
    app = open_page("Overview")
    click(app, "Reset everything")
    assert httpx.get(f"{api}/metrics").json()["requests"]["total"] == 0
    assert httpx.get(f"{api}/health").json()["release_version"] == "v1.0.0"
