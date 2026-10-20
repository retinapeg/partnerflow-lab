# GOAL continuation decisions

- Continue on the existing `main` branch, commit locally after each green step,
  and stop when the GOAL checklist is complete. Do not push.
- The initial dirty files (`analysis/hier.py`, `tests/test_hier.py`,
  `tests/conftest.py`, `pyproject.toml`, `uv.lock`, `ARCHITECTURE.md`) are the
  unfinished hierarchical step. Preserve and validate them as part of this
  continuation; no unrelated initial changes were found.
- Fit the hierarchy to untreated days 0–27 so its partner rates match the
  baseline truth. Report 94% partner intervals and their empirical coverage;
  the methods table uses a 95% interval for the unweighted mean partner rate.
- Keep the existing ArviZ constraint and lockfile. Use the repository's Python
  3.12 environment and local numerical caches; no global configuration changes.
- Initial collection failed because installed Numba 0.63.0b1 references
  `numpy.row_stack`, absent in NumPy 2.5.4. Constrain NumPy to `<2.4`, regenerate
  the lockfile, and sync the local environment before validating the hierarchy.
- The next run reached sampling but system clang could not find the C++
  `<vector>` header. Default this small model to PyTensor's Python execution
  (`cxx=`), overridable through `PYTENSOR_FLAGS`, instead of changing system
  developer tools. Sampling remains seeded, with two sequential chains.
- The current request authorizes completing all remaining GOAL items serially,
  superseding the older division-of-labour suggestion in PLAN.md. The Learning
  paragraph will describe project lessons, without claiming Leo wrote it or
  has personally mastered them.
- Hierarchical checkpoint: 23 tests passed, including baseline truth recovery,
  94% partner-interval coverage and sampler diagnostics (313.70 seconds).
- Commit blocker: automatic approval review rejected `git commit` with
  `approval required by policy, but AskForApproval is set to Never`. The user
  requested commits, but this execution restriction prevents them. Preserve
  the green checkpoint boundaries here and leave the final changes staged;
  do not bypass the restriction or claim commits were created.
- API choices: read the generated parquet snapshot once during startup; require
  generation before launch. Reuse existing power and A/B functions. Define
  automatic routing share among data-complete journeys and zero-denominator
  stage rates as zero. SCHEMA.md was expanded to document these choices,
  validation constraints and existing response models; no GOAL route was added.
- API checkpoint: all 27 endpoint tests passed. The installed Starlette emits
  an httpx TestClient deprecation warning; validation succeeds with the existing
  locked dependency set, so no unrelated client migration was introduced.
- Tests checkpoint: the complete suite passed all 56 tests in 295.87 seconds,
  including estimator truth checks, simulator file reproducibility, 27 endpoint
  tests and six report checks. One TestClient deprecation warning remains.
- Report checkpoint: `python -m analysis.report` populated README.md from the
  complete passing seed-0 artifact in `data/method-results.json`. It rejects
  failed runs, missing/duplicate methods, non-finite estimates and inconsistent
  biases; tests verify prose preservation and idempotent replacement.
- Documentation checkpoint: README.md includes local run commands, the generated
  table, a Learning paragraph and an evidence-linked ML/DS learning log. Reviewed
  the commands against the actual modules and contracts. Data generation produced
  137,370 journeys; its existing eager package import emits a runpy warning,
  while the reproducibility test passes.
- All GOAL boxes are now ticked. Stop here; Moonshot and Mars remain untouched.
  All continuation changes are staged, with no new commits or pushes because of
  the approval-policy blocker described above.
