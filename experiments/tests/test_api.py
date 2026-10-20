import pytest
from fastapi.testclient import TestClient

from analysis.ab import analyse
from analysis.power import calculate_power
from api.main import create_app
from api.models import AnalyseResult, Error, Funnel, Health, PowerResult


@pytest.fixture(scope="module")
def client(simulation, tmp_path_factory):
    path = tmp_path_factory.mktemp("api") / "events.parquet"
    simulation[0].to_parquet(path, index=False)
    with TestClient(create_app(path)) as client:
        yield client


def test_health_contract(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert Health.model_validate(response.json()).model_dump() == {
        "status": "ok", "version": "0.1.0",
    }


@pytest.mark.parametrize("partner_id", [0, 7, 39])
def test_funnel_contract_and_denominators(client, simulation, partner_id):
    response = client.get(f"/v1/funnel/{partner_id}")
    assert response.status_code == 200
    result = Funnel.model_validate(response.json())
    partner = simulation[0].query("partner_id == @partner_id")
    assert result.partner_id == partner_id
    assert result.n == len(partner)
    previous = len(partner)
    for name, stage in result.stages.model_dump().items():
        column = name if name == "repeat_90d" else f"stage_{name}"
        count = int(partner[column].sum())
        assert stage["count"] == count
        assert stage["rate_from_previous"] == pytest.approx(count / previous)
        assert stage["rate_from_start"] == pytest.approx(count / len(partner))
        previous = count
    assert result.routed_auto_share == pytest.approx(
        partner.routed_auto.sum() / partner.stage_data_complete.sum())


def test_zero_denominators(simulation, tmp_path):
    events = simulation[0].iloc[:1].copy()
    for column in ("stage_data_complete", "stage_offer", "stage_accepted",
                   "stage_drawdown", "repeat_90d", "routed_auto"):
        events[column] = False
    path = tmp_path / "events.parquet"
    events.to_parquet(path)
    with TestClient(create_app(path)) as client:
        response = client.get(f"/v1/funnel/{events.partner_id.iloc[0]}")
    assert response.status_code == 200
    result = Funnel.model_validate(response.json())
    assert result.routed_auto_share == 0
    assert all(stage["rate_from_previous"] == 0 for stage in result.stages.model_dump().values())


@pytest.mark.parametrize("partner_id,status", [(40, 404), (-1, 404), ("abc", 422)])
def test_partner_errors(client, partner_id, status):
    response = client.get(f"/v1/funnel/{partner_id}")
    assert response.status_code == status
    Error.model_validate(response.json())


def test_power_contract(client):
    body = {"baseline_rate": 0.16, "mde_pp": 4, "daily_traffic": 100}
    response = client.post("/v1/experiments/power", json=body)
    assert response.status_code == 200
    result = PowerResult.model_validate(response.json())
    assert result.model_dump() == calculate_power(**body)


@pytest.mark.parametrize("planned,ready", [(None, False), (100, True), (101, False)])
def test_analyse_contract_and_readiness(client, planned, ready):
    body = {"control": {"n": 100, "conversions": 16},
            "treatment": {"n": 100, "conversions": 20}}
    if planned is not None:
        body["planned_n_per_arm"] = planned
    response = client.post("/v1/experiments/analyse", json=body)
    assert response.status_code == 200
    result = AnalyseResult.model_validate(response.json())
    assert result.model_dump(mode="json") == analyse(**body)
    assert result.ready_to_read is ready


@pytest.mark.parametrize("changes", [
    {"baseline_rate": 0}, {"mde_pp": -1}, {"mde_pp": 90},
    {"alpha": 0.9}, {"daily_traffic": 0}, {"power": 1}, {"unknown": 1},
])
def test_power_validation(client, changes):
    body = {"baseline_rate": 0.2, "mde_pp": 4, "daily_traffic": 100, **changes}
    response = client.post("/v1/experiments/power", json=body)
    assert response.status_code == 422
    Error.model_validate(response.json())


@pytest.mark.parametrize("changes", [
    {"control": {"n": 0, "conversions": 0}},
    {"control": {"n": 10, "conversions": 11}},
    {"control": {"n": 10, "conversions": -1}},
    {"control": {"n": 10.5, "conversions": 1}},
    {"control": {"n": True, "conversions": 1}},
    {"planned_n_per_arm": 0}, {"planned_n_per_arm": 1.5},
])
def test_analyse_validation(client, changes):
    body = {"control": {"n": 10, "conversions": 1},
            "treatment": {"n": 10, "conversions": 2}, **changes}
    response = client.post("/v1/experiments/analyse", json=body)
    assert response.status_code == 422
    Error.model_validate(response.json())


def test_openapi_goal_routes_and_response_schemas(client):
    paths = client.get("/openapi.json").json()["paths"]
    expected = {
        "/health": ("get", "Health"),
        "/v1/funnel/{partner_id}": ("get", "Funnel"),
        "/v1/experiments/power": ("post", "PowerResult"),
        "/v1/experiments/analyse": ("post", "AnalyseResult"),
    }
    assert set(paths) == set(expected)
    for path, (method, model) in expected.items():
        schema = paths[path][method]["responses"]["200"]["content"]["application/json"]["schema"]
        assert schema["$ref"] == f"#/components/schemas/{model}"
