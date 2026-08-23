# Ostiari Escape Lab

A provider-neutral agent-containment benchmark and incident-to-regression
system based on `Ostiari_Escape_Lab_Spec_v0.1.docx`.

Escape Lab tests whether a long-running, tool-using agent can cause a prohibited
state transition outside its explicit authority—and whether layered controls
detect, interrupt, explain, and recover from the attempt.

![Ostiari Escape Lab system architecture](docs/diagrams/escape-lab-architecture.png)

The diagram is maintained as an editable
[Draw.io source](docs/diagrams/escape-lab-architecture.drawio). See the
[architecture guide](docs/architecture.md) for the component and run-lifecycle
views.

This repository is a runnable research MVP. Its default scripted agent and
synthetic range are deliberately non-offensive:

- command strings are modeled, never executed;
- tool calls never open network connections;
- every credential, identity, service, and target is synthetic;
- C0 requires an explicit opt-in;
- evidence is stored outside the modeled agent boundary in a SHA-256 hash chain.

## What is included

- Twelve reviewed scenarios mapped one-to-one from S01/E01 through S12/E12.
- Five T2-designated scenario contracts and seven T1 contracts.
- C0–C4 control-profile ablation.
- Static authority, per-action risk, trajectory aggregation, source-to-sink
  controls, delegated-worker inheritance, scoped approvals, redaction, circuit
  breaking, rollback, and teardown.
- Append-only event evidence, snapshots, integrity validation, JSON results, and
  human incident briefs.
- Paired experiments with randomized profile order and Wilson confidence
  intervals.
- Frozen-run replay and reproducibility checks.
- A separate append-only disclosure-status ledger.
- An optional bridge to the existing Ostiari `Guard.validate` API.
- Docker T2 hardening assets and preflight checks.

## Current boundary

The deterministic runner is suitable for T0/T1 contract development,
regression testing, demonstrations, and control ablation. T2 manifests are
implemented and Docker security posture is specified, but real container or
micro-VM execution still requires an independently reviewed runner and
isolation validation. The project does not claim production certification,
universal agent safety, or safe handling of arbitrary exploit payloads.

See [requirements traceability](docs/requirements-traceability.md) for the exact
implemented and pending scope.

## Deterministic reference results

The current regression gold set contains one fixed-seed run for each of the
twelve scenarios under every C0–C4 profile:

![Deterministic control-profile comparison](docs/diagrams/control-profile-results.png)

| Profile | Material escapes | Containment failure rate | Authorized task completion |
|---|---:|---:|---:|
| C0 | 12 / 12 | 100% | 100% |
| C1 | 12 / 12 | 100% | 100% |
| C2 | 0 / 12 | 0% | 100% |
| C3 | 0 / 12 | 0% | 100% |
| C4 | 0 / 12 | 0% | 91.7% |

C4 completes 11 of 12 tasks because S12 deliberately triggers the
evidence-loss circuit breaker. These are deterministic synthetic-runner results,
not measurements of a live model or production sandbox. See the
[complete results, outcome matrix, confidence intervals, and limitations](docs/results.md).
The graph also has an editable
[Draw.io source](docs/diagrams/control-profile-results.drawio).

## Quick start

Python 3.10 or newer is required. The core project has no third-party runtime
dependencies.

```bash
cd /Users/harlnk/aws/OstiariEscapeLab

PYTHONPATH=src python3.12 -m escape_lab validate
PYTHONPATH=src python3.12 -m escape_lab list
PYTHONPATH=src python3.12 -m escape_lab run S06 --profile C4
PYTHONPATH=src python3.12 -m escape_lab demo
```

Or install it locally:

```bash
python3.12 -m pip install -e .
escape-lab validate
escape-lab demo
```

## Control profiles

| Profile | Behavior |
|---|---|
| C0 | Synthetic infrastructure isolation only. Explicit `--allow-c0` required. |
| C1 | Static action, resource, destination, and identity scopes. |
| C2 | C1 plus deterministic per-action semantic risk. |
| C3 | C2 plus cross-step risk, source-to-sink analysis, actual-route checks, and delegated-worker linkage. |
| C4 | C3 plus scope-bound approvals, redaction, circuit breakers, rollback, and recovery verification. |

## Common workflows

Run one scenario:

```bash
PYTHONPATH=src python3.12 -m escape_lab run S01 --profile C4 --seed 17
```

Run a reviewed C0 baseline:

```bash
PYTHONPATH=src python3.12 -m escape_lab run S01 --profile C0 --allow-c0
```

Compare profiles with paired trials:

```bash
PYTHONPATH=src python3.12 -m escape_lab compare S01 S06 S09 \
  --profiles C1,C2,C3,C4 \
  --trials 30 \
  --seed 100
```

Replay a frozen run:

```bash
PYTHONPATH=src python3.12 -m escape_lab replay artifacts/runs/<run-id>
```

Verify evidence:

```bash
PYTHONPATH=src python3.12 -m escape_lab verify artifacts/runs/<run-id>
```

Advance a finding through disclosure:

```bash
PYTHONPATH=src python3.12 -m escape_lab disclosure \
  artifacts/runs/<run-id> acknowledged \
  --note "Provider confirmed receipt"
```

Request termination of a running experiment:

```bash
PYTHONPATH=src python3.12 -m escape_lab kill <run-id> \
  --reason "operator stop condition"
```

## Existing Ostiari integration

The reference controls are dependency-free and deterministic. To add the
current Ostiari Guard result to C2–C4 scoring:

```bash
PYTHONPATH=src python3.12 -m escape_lab \
  --backend ostiari \
  --ostiari-src /Users/harlnk/aws/ostiari \
  run S06 --profile C4
```

The bridge is reconciled with Ostiari 0.1.x:

- `Guard.validate(action, params, context)`
- `ValidationResult.original_tier`
- `ActionBlockedError.original_tier`
- fail-closed `OstiariConfig`

The lab keeps its own out-of-band evidence and outcome assertions because a
runtime guard trace alone is not sufficient to adjudicate achieved state.

## Evidence package

Every run writes:

```text
artifacts/runs/<run-id>/
├── run-manifest.json
├── events.jsonl
├── integrity.json
├── result.json
├── incident-brief.md
└── snapshots/
    ├── before.json
    ├── after.json
    └── post-teardown.json
```

`events.jsonl` contains ordered tool requests, policy decisions, approvals,
interventions, results, state assertions, teardown, and final outcome. Each
record includes the previous record hash and its own content hash.

## T2 Docker posture

Build the minimal range image:

```bash
docker build -t escape-lab-range:0.1.0 docker/range
```

Inspect or run the hardened preflight:

```bash
PYTHONPATH=src python3.12 -m escape_lab preflight \
  --docker-image escape-lab-range:0.1.0

PYTHONPATH=src python3.12 -m escape_lab preflight \
  --docker-image escape-lab-range:0.1.0 \
  --probe-docker
```

The generated posture uses no network, a read-only root filesystem, no Linux
capabilities, `no-new-privileges`, a non-root UID, resource limits, and a small
`noexec` temporary filesystem.

## Tests

```bash
make test
```

The test suite verifies catalog acceptance, the complete C0/C4 scenario matrix,
profile differentiation, approval binding, evidence tamper detection, replay,
disclosure integrity, and CLI behavior.

## Project layout

```text
src/escape_lab/       Runtime, controls, evidence, CLI, and reports
scenarios/            Versioned scenario catalog
schemas/              Resolved scenario JSON Schema
tests/                Standard-library regression suite
docs/                 Architecture, safety, and traceability
docs/diagrams/        Editable Draw.io sources and exported PNGs
docker/range/         Hardened T2 image starting point
```

## Safety and non-goals

Escape Lab must not contain production credentials, personal data, live
third-party targets, uncontrolled internet access, undisclosed exploit code, or
instructions that materially increase offensive capability. It is not a
jailbreak leaderboard, exploit generator, vendor ranking, or proof that a model
is generally safe.
