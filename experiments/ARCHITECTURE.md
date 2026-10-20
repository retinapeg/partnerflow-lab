# Architecture and Git checkpoints

Work stays on one branch (`main`); nothing is pushed. Each completed step is
tested before its checkpoint commit. This diagram is updated in subsequent
checkpoints, so Git preserves the architecture alongside the implementation.
The first four snapshots below record the checkpoints already completed when
Leo requested architecture history.

## Current architecture

```mermaid
flowchart LR
    Seed[Seed 0] --> Sim[sim.generate]
    Sim --> Events[events.parquet: simulated journeys]
    Sim --> Truth[truth.json: known effects and rates]
    Inputs[Baseline, MDE, traffic] --> Power[Two-proportion power]
    Events --> AB[Randomised top-up A/B]
    Events --> Naive[Biased prequal before/after]
    Events --> DID[Prequal TWFE DiD]
    Events --> Baseline[Untreated days 0–27]
    Baseline --> Hier[Hierarchical Beta-Binomial]
    Hier --> PartnerPosteriors[Partner means and 94% intervals]
    PartnerPosteriors --> Tests
    Power --> Tests[pytest validation]
    AB --> Tests
    Naive --> Tests
    DID --> Tests
    Truth --> Tests
    Events --> API[FastAPI: funnel and health]
    Power --> API
    AB --> API
    Contracts[Pydantic models and SCHEMA.md] --> API
    API --> APITests[TestClient schema and behaviour checks]
    Tests --> Evidence[Passing pytest JSON]
    APITests --> Evidence
    Evidence --> Report[analysis.report]
    Report --> README[Generated methods-vs-truth table]
```

## Earlier checkpoint snapshots

The GOAL continuation completed the hierarchy, API, test and report stages,
but its commit attempts were blocked by the session approval policy. Those
green checkpoints are recorded in `NOTES.md`; they have no commit IDs yet.

`b02e8ee` — simulator; two tests passed.

```mermaid
flowchart LR
    Seed --> Simulator --> Events
    Simulator --> Truth
    Events --> ReproducibilityAndFunnelTests
```

`c832b3d` — power; ten cumulative tests passed.

```mermaid
flowchart LR
    Simulator --> EventsAndTruth --> PowerSimulationTest
    BaselineMDETraffic --> Power --> PowerSimulationTest
```

`5b0422b` — A/B; eight focused tests passed.

```mermaid
flowchart LR
    Simulator --> EligibleRandomisedArms --> AB --> TruthRecoveryTest
    Truth --> TruthRecoveryTest
```

`57c6ea4` — naive benchmark; two focused tests passed.

```mermaid
flowchart LR
    Simulator --> BeforeAfterGroups --> Naive --> BiasTest
    Truth --> BiasTest
```

## Inspecting or undoing a checkpoint

Use `git log --oneline` to locate checkpoints and `git show <commit>` to inspect
them. A later, explicitly chosen `git revert <commit>` can undo a change with a
new commit while preserving history. No rollback is performed automatically.
`uv.lock` pins dependencies; generated `data/` files and virtual environments
are not versioned. Regenerate seed-0 data after restoring code, and sync the
environment from the restored lockfile. The original Python 3.11 environment
is preserved in ignored `.venv-python311/`.
