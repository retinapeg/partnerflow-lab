import json
from pathlib import Path

import pytest

from sim.generate import write_data


def pytest_addoption(parser):
    parser.addoption("--method-results", help="Write passing estimator evidence as JSON")


@pytest.fixture(scope="session")
def method_rows():
    return []


@pytest.fixture
def record_result(method_rows, request):
    request.config._method_rows = method_rows

    def record(method, estimate, ci95, truth, metadata=None):
        method_rows.append(dict(method=method, estimate=float(estimate),
                                ci95=[float(x) for x in ci95], truth=float(truth),
                                bias=float(estimate - truth), metadata=metadata or {}))

    return record


def pytest_sessionfinish(session, exitstatus):
    destination = session.config.getoption("--method-results")
    if destination:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "seed": 0, "exitstatus": int(exitstatus),
            "tests_collected": session.testscollected,
            "rows": getattr(session.config, "_method_rows", []),
        }, indent=2) + "\n")


@pytest.fixture(scope="session")
def simulation(tmp_path_factory):
    output = tmp_path_factory.mktemp("simulation")
    events, _ = write_data(seed=0, output=output)
    # Estimator assertions read persisted truth, not duplicated constants.
    truth = json.loads((output / "truth.json").read_text())
    return events, truth
