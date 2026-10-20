import json

import pytest

from analysis.report import END, METHODS, START, render, write_report


@pytest.fixture
def evidence():
    # Formatting fixtures only; actual README evidence comes from estimator tests.
    return {"exitstatus": 0, "seed": 0, "tests_collected": 6, "rows": [
        {"method": method, "estimate": 2.5, "truth": 2.0, "bias": 0.5,
         "ci95": [1.0, 3.0], "metadata": {
             "calibration94": 0.95, "max_rhat": 1.01,
             "min_bulk_ess": 500, "divergences": 0,
         }} for method in METHODS
    ]}


@pytest.mark.parametrize("failure", ["failed", "partial", "duplicate", "nonfinite", "bias"])
def test_reject_unusable_evidence(evidence, failure):
    if failure == "failed":
        evidence["exitstatus"] = 1
    elif failure == "partial":
        evidence["rows"].pop()
    elif failure == "duplicate":
        evidence["rows"][-1] = evidence["rows"][0]
    elif failure == "nonfinite":
        evidence["rows"][0]["estimate"] = float("nan")
    else:
        evidence["rows"][0]["bias"] = 999
    with pytest.raises(ValueError):
        render(evidence)


def test_report_preserves_prose_and_is_idempotent(evidence, tmp_path):
    results = tmp_path / "results.json"
    readme = tmp_path / "README.md"
    results.write_text(json.dumps(evidence))
    readme.write_text(f"Introduction\n{START}\nold table\n{END}\nLearning\n")
    write_report(results, readme)
    first = readme.read_text()
    write_report(results, readme)
    assert readme.read_text() == first
    assert first.startswith("Introduction\n") and first.endswith("\nLearning\n")
    assert "| TWFE DiD: drawdown | 2.50 | [1.00, 3.00] | 2.00 | +0.50 |" in first
