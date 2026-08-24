# Requirements traceability

Status meanings:

- **Implemented**: runnable and covered by the local regression suite.
- **Partial**: contract and safe reference implementation exist; production
  hardening or external integration remains.
- **Pending**: intentionally outside this repository's current MVP.

## Functional requirements

| Requirement | Status | Implementation |
|---|---|---|
| FR-001 Scenario registry | Implemented | Resolved versioned catalog, semantic versions, provenance, review status, digest, validation. |
| FR-002 Environment provisioning | Partial | Disposable in-process ranges, an executable Docker/gVisor range worker, and a separate gVisor worker for the reviewed offline AxonLLM fixture use immutable image resolution, dual in-boundary/container-contract attestation, limits, and verified removal; arbitrary/live-agent workers and independent certification remain pending. |
| FR-003 Provider adapters | Partial | Deterministic scripted adapter plus AxonLLM fixture/live tool loop; the reviewed fixture can run with an in-process credential-free provider inside gVisor. Additional direct agent-framework adapters and credentialed live qualification remain. |
| FR-004 Action interception | Partial | Every scripted/AxonLLM tool and delegated action is intercepted, selected range effects execute in the isolated range worker, and the isolated fixture agent communicates only through bounded RPC. Direct syscalls are confined by gVisor but not semantically intercepted; arbitrary non-mediated process/network activity remains outside current qualification. |
| FR-005 Policy decision | Implemented | Allow, deny, redact, approve, suspend, terminate records with rationale and provenance. |
| FR-006 Trajectory monitoring | Implemented | Cross-step risk, source/sink labels, effective routes/resources, actor/parent linkage. |
| FR-007 Scoped approval | Implemented | HMAC binding to action, parameters, resources, destination, trajectory, parent, and expiry. |
| FR-008 Circuit breaking | Implemented | Budgets, operator kill file, aggregate risk, and evidence-loss termination. |
| FR-009 Rollback | Implemented | Pre-action checkpoints, post-condition rollback, teardown verification. |
| FR-010 Evidence capture | Implemented | Ordered out-of-band JSONL, snapshots, argument/result digests, SHA-256 chain. |
| FR-011 Replay | Implemented | Frozen manifest/seed/profile replay with digest, environment, outcome, and assertion comparison. |
| FR-012 Reporting | Implemented | JSON result, event bundle, incident brief, experiments, control comparison. |
| FR-013 Kill switch | Implemented | Independent artifact control file checked before every action. |
| FR-014 Disclosure workflow | Implemented | Append-only private/acknowledged/remediated/coordinated/publishable ledger. |
| FR-015 Release regression gate | Implemented | Versioned thresholds, non-zero regression exit, JSON/Markdown/HTML/JUnit reports, and GitHub Actions integration. |

## Non-functional requirements

| Requirement | Status | Implementation |
|---|---|---|
| NFR-01 Isolation | Partial | Executable range and fixture-agent gVisor backends use no non-loopback network, read-only root, non-root UID, zero capabilities, resource limits, boundary verification, and forced teardown. The fixture agent additionally requires Docker contract attestation for runtime, network, mounts, identity inputs, and settings hidden by gVisor's emulated `/proc`. Arbitrary/live-agent isolation, micro-VM support, and independent production validation remain pending. |
| NFR-02 Reproducibility | Implemented | Manifest, environment digest, profile, backend, model adapter, and seed frozen per run. |
| NFR-03 Observability | Implemented | Timestamped correlated events and protected-state snapshots. |
| NFR-04 Portability | Implemented | Self-contained Python 3.10+ wheel with packaged catalog/schema/baseline and Linux-compatible Docker posture. |
| NFR-05 Privacy | Implemented | Synthetic fixtures, no private reasoning, sensitive-key redaction before evidence writes. |
| NFR-06 Extensibility | Implemented | Data-driven scenario/assertion contracts and modular control/range adapters. |
| NFR-07 Performance | Partial | Decision latency recorded; production load and timeout characterization pending. |
| NFR-08 Integrity | Implemented | Append-only SHA-256 event and disclosure chains. |

## Acceptance criteria

| Criterion | Status | Evidence |
|---|---|---|
| AC-01 Twelve E01–E12 scenarios, at least four T2 | Implemented | Registry validates 12 mappings and five T2 contracts. |
| AC-02 C1–C4 share one contract; reviewed C0 | Implemented | One manifest drives all profiles; C0 needs explicit opt-in. |
| AC-03 No public internet, production identity, host filesystem, or evidence store | Partial | Structurally true for the synthetic runner and enforced for the OCI range and gVisor fixture-agent worker with no host mounts, no non-loopback route, identity-absence probes, and evidence kept outside; arbitrary/live-agent isolation and an external audit remain pending. |
| AC-04 All tool/delegated actions correlated; evidence loss fails closed | Implemented | Parent IDs, trajectory IDs, evidence assertions, C4 evidence breaker. |
| AC-05 O0–O4 deterministic gold set | Implemented | Assertion engine and scenario-matrix tests; external reviewer agreement pending. |
| AC-06 Teardown verifies cleanup | Implemented | Process, filesystem, identity, network, and range-root checks. |
| AC-07 Frozen replay | Implemented | Replay command and test. |
| AC-08 No live secrets, personal data, or operational exploit detail | Implemented | Synthetic-only catalog and preflight. |
| AC-09 Three-scenario private demo and report without repair | Implemented | `escape-lab demo` runs S03, S06, and S09 across C1–C4. |
| AC-10 AxonLLM CI release gate | Implemented | S03/S06/S09 traverse AxonLLM routing/tool translation under C4; adapter identity, optional required agent runtime, model turns, containment, task, evidence, and validity are gated. |

## Deliberately pending production work

- Dedicated container or micro-VM execution for arbitrary or live agent/model
  processes beyond the reviewed deterministic fixture.
- Independent runtime/kernel isolation assessment and escape-resistance testing.
- Kernel/network interception for capabilities that bypass mediated tools.
- Multi-reviewer adjudication workflow and reviewer identity management.
- Durable remote artifact storage, retention enforcement, and access control.
- T3 cyber-range authorization and operations.
- Credentialed live-provider qualification and statistical calibration against
  non-deterministic models.
