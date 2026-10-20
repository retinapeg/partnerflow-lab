# partner-flow-lab — rules for Claude Code / Codex

Read PLAN.md and SCHEMA.md before writing code. Build the GOAL tier only unless told otherwise.

## What this is
A partner-referred lending funnel simulator with known treatment effects, estimators that try to recover them, and a FastAPI service + Streamlit dashboard on top. All data is simulated. Never name a real lender, partner or brand; say "inspired by embedded-lending partner funnels".

## Hard rules
- Python 3.12, `.venv` in the repo root, deps in `pyproject.toml` (uv or pip). Never install globally.
- Layout: `sim/`, `analysis/`, `api/`, `app/`, `tests/`, `data/` (gitignored except `data/README.md`), `SCHEMA.md`, `PLAN.md`, `CHANGELOG.md`, `README.md`.
- Every estimator in `analysis/` has a test in `tests/` that runs it on `sim.generate(seed=0)` and asserts the estimate is within tolerance of `data/truth.json` (tolerance stated in the test). A method that is expected to be wrong (naive before/after) has a test asserting it IS biased.
- Every endpoint has a schema test (pydantic models in `api/models.py`, mirrored in SCHEMA.md). Do not change SCHEMA.md without saying so.
- Seeded everything. `python -m sim.generate --seed 0` must be reproducible.
- No notebooks in the repo except `notebooks/explore.ipynb`; final code lives in `.py`.
- Commit after each green step with a one-line message. Do not push.
- Write the methods-vs-truth table into README.md from the test output (a script, `analysis/report.py`), never by hand.
- Stop when the GOAL stopping condition in PLAN.md is met. Report what is done, what is not, and the table.
