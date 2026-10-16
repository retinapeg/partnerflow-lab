"""Shared test setup. Every test gets its own empty SQLite file."""

import pytest

from partnerflow import config


@pytest.fixture
def db_file(tmp_path, monkeypatch):
    """Point the app at a temporary database for the duration of one test."""
    path = tmp_path / "lab.db"
    monkeypatch.setenv("PARTNERFLOW_DB", str(path))
    return path


@pytest.fixture
def client(db_file):
    """An in-process client for the API. No server or network is needed."""
    from fastapi.testclient import TestClient

    import main
    from partnerflow import faults

    faults.reset_runtime_state()
    with TestClient(main.app) as test_client:
        yield test_client


def key(partner_id: str) -> dict:
    """Request headers carrying the partner's demo key."""
    return {"X-API-Key": config.PARTNER_BY_ID[partner_id]["api_key"]}


@pytest.fixture
def alder():
    return key("alder")


@pytest.fixture
def birch():
    return key("birch")


@pytest.fixture
def cedar():
    return key("cedar")
