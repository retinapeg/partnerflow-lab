# Bounded improvement hand-off — 3 November 2026

Work is local on `iwoca-depth`. No push, deployment, email or application was made.
All results describe fictional, simulated data.

## Ownership and inherited work

The starting branch was `engineering/delayed-outcomes-cca43d1c` at `4a8a307`.
Its 23 modified tracked files and 9 untracked files were preserved byte-for-byte
in checkpoint `e97ebf8`; every file was checked against a SHA-256 manifest saved
before editing. The separate snapshot is `/tmp/partnerflow-depth/inherited/`.
The checkpoint contains inherited statistical interpretation, validation code,
results, decision brief, dashboard changes and dependency-lock repair. Those are
not new implementations from this pass.

New work is limited to the following three audit candidates:

1. **Numerical failure handling** (`173945f`): reject indistinguishable rates,
   solver nonconvergence, incorrect/nonfinite solver results and nonfinite
   recruitment time. The experiments API converts these into a documented 422.
   `experiments/SCHEMA.md` was updated; successful response fields are unchanged.
2. **CLI/API evidence agreement** (`0eb3028`): retrieve each run's log from the
   same API that handled the requests, normalise it through the shared analytics
   loader, check control/evidence responses, and validate complete runs before
   printing comparisons. The CLI no longer reads a local database for evidence.
3. **Persistent replay settings** (`0eb3028`): record count and seed in request
   metadata, recover them in fresh dashboard sessions, and validate unique
   journeys, expected releases and application partner/product/amount/term
   values. Missing, duplicate, partial or inconsistent evidence blocks replay
   and offers an explicit lab reset. The intentional R-204 fault is unchanged.

## Reproductions before edits

Both review-only findings were reproduced dynamically against disposable API
instances before changing their implementation:

- With a different, empty CLI database, all three API runs completed but the CLI
  raised `IndexError: single positional indexer is out-of-bounds` when reporting.
- A baseline of 60 journeys, seed 19, reopened in a fresh dashboard session as
  150 journeys, seed 7. Actual after-release applications differed: journey zero
  changed from Birch / credit line / £18,000 to Alder / term loan / £27,000.
- The numerical audit was also reconfirmed: MDE `1e-10` returned 10 participants
  per arm and one day after a convergence warning; `1e-20` raised `ValueError`;
  daily traffic `1e-320` raised `OverflowError`.

Reproduction output is retained in `/tmp/partnerflow-depth/reproductions.log`.
Permanent regression tests now cover these cases and their failure paths.

## Verification

- Original main suite: **152 passed in 30.19 seconds**.
- Final main suite: **174 passed in 50.20 seconds**, including real HTTP API and
  headless Streamlit interaction, all dashboard pages, two fresh-session replays,
  legacy-log reset, malformed/incomplete replay evidence and CLI control failures.
- Focused numerical/API checks: **45 passed**, including the original 20,000-draw
  power check. No statistical assertions or sampler diagnostics were relaxed.
- Full experiments baseline: **66 passed, one dependency deprecation warning,
  in 299.38 seconds**. Its generated evidence exactly matches the inherited
  method-results file: maximum R-hat 1.00289, minimum bulk ESS 5113.56, zero
  divergences, 94% partner-interval coverage 92.5%.
- Expanded final experiments suite: **76 passed, one dependency deprecation
  warning, in 256.85 seconds**. The six estimator rows and model diagnostics
  match the baseline exactly. `results/method-results.json` was saved from this
  passing run and `analysis.report` regenerated the README table (76 tests).
  The warning concerns Starlette TestClient/httpx compatibility; no check failed.
- Full drawdown validation rerun: **200/200 scenario/seed combinations**, fixed
  seeds 1000–1099 in both scenarios, plus the seed-0 comparison. The regenerated
  CSV, validation JSON, summary and decision brief are byte-identical to the
  inherited files. All recorded source hashes still match. CSV SHA-256:
  `a00639b9e33b7c2ceadfcbe75e57e8364f1744f57c5ccb643ff91db09579082b`.
- Real `./start.sh --no-browser` smoke check: API and Streamlit health passed;
  `demo.py` succeeded with a deliberately different local DB path. Each run sent
  150 applications: **6 → 41 → 16** refusals; **35** newly refused after release;
  **10** remained newly refused after the fix, all invoice finance. API ended on
  v1.1.1; both child services were stopped cleanly.
- `git diff --check` passed. No global packages were installed.

Commands used (from the repository root unless noted):

```sh
.venv/bin/python -m pytest -q
# From experiments/, using its existing Python 3.12 environment:
MPLCONFIGDIR=/tmp/partnerflow-depth/matplotlib \
PYTENSOR_FLAGS='base_compiledir=/tmp/partnerflow-depth/pytensor-final,cxx=' \
.venv/bin/python -m pytest tests -q \
  --method-results=/tmp/partnerflow-depth/final-method-results.json
.venv/bin/python -m analysis.validate_drawdown \
  --output /tmp/partnerflow-depth/drawdown
```

Logs and isolated outputs are in `/tmp/partnerflow-depth/`. The initial experiments
attempt enabled C++ compilation and failed because the local toolchain was not
usable; subsequent runs use the repository's existing portable `cxx=` mode.
The Bayesian model, seeds, draw counts and diagnostic thresholds are unchanged.

## Limits

The dashboard still reads SQLite for analysis, so its `PARTNERFLOW_DB` must match
the API's. `start.sh` starts both with the same environment. The CLI uses HTTP
for evidence and has no such requirement. Old logs cannot recover a missing seed;
they require an explicitly selected reset, which deletes that demo lab's data.
Use an isolated, disposable lab and avoid concurrent traffic or configuration
changes during the controlled story. This pass does not add a distributed run
coordinator or validate a remote deployment. Headless interaction and server
health were checked; visual browser layout was not reviewed in this pass.

The normal-approximation power calculator rejects numerically unsupported designs;
it does not provide arbitrary-precision planning for extremely small effects.
Recruitment time excludes outcome follow-up. Synthetic estimates and replayed
requests do not establish commercial uplift, profit or real-world causal validity.
No clean-environment installation was repeated; the existing environments were used.

## Two-minute interview explanation

“PartnerFlow Lab is a fictional lending simulation with two purposes: diagnosing
integration regressions and testing whether an intervention's apparent benefit
is credible. I inherited the simulator and validation evidence, then made three
bounded corrections to the reliability of the demonstration.

First, I reproduced a numerical failure where an extremely small target effect
made the power solver warn and return ten participants per arm. The calculator
now rejects failed or unsupported calculations, and the API returns a clear
validation error. A plausible-looking number is not enough evidence that a
numerical method succeeded.

Second, I found that the CLI could send requests to one API and report from a
different database. It now reads the request log from that same API and checks
the evidence before presenting it. Third, I reproduced a fresh browser session
silently changing the replay's seed and sample count. Those settings now persist
with the log, and the dashboard checks the actual application identities.

The release story sends the same 150 journeys through a baseline, an intentional
regression and a patch. Refusals go from six to forty-one to sixteen. The fix
restores eligible products while retaining the intended invoice-finance rule.
That is functional evidence on fixed requests, not a population experiment.

On the causal side, rerunning all two hundred simulated cases reproduced the
saved results. Coverage fell from ninety-four percent to nine percent when an
untreated trend violated the model's assumptions. Precision cannot rescue an
invalid counterfactual. A real rollout decision would still need randomisation,
complete follow-up, credit-risk guardrails and positive contribution after
losses and costs.”

## Short demo walkthrough

1. In a terminal, set `PARTNERFLOW_DB` to a fresh disposable path and run
   `./start.sh`. The default dashboard is http://127.0.0.1:8510.
2. Run `.venv/bin/python demo.py` against that API. It resets the API lab;
   use `PARTNERFLOW_API_URL` if using a nondefault port. Show the 6/41/16 table.
3. Open **Release simulation**. Show the matched regressions and the ten retained
   invoice-finance rejections. For a settings demonstration, reset explicitly,
   choose 60 journeys / seed 19, create a baseline, reopen the page in a fresh
   session, and replay. Count and seed stay locked to the recorded baseline.
4. Open the generated decision brief and validation summary. Explain the
   94/100 versus 9/100 coverage comparison and the assumptions behind it.
   The longer three-minute narrative remains in `DEMO_SCRIPT.md`.

## Shared hand-off

A factual one-line completion entry was appended through automatic approval
review to the [shared hand-off](https://docs.google.com/document/d/1gqAB9aNMXRt48r-y6QRc-BBy6pjNBFdZsd0NHV_a5eU).
Connector readback verified the exact new entry and that all prior document-body
content and structure were unchanged. No other agent's section was edited.
