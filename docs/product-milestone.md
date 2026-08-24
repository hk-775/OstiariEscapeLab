# First product milestone

The first product milestone turns Escape Lab from a source-checkout benchmark
into an installable containment release gate for AxonLLM.

![AxonLLM CI release gate](diagrams/ci-release-gate.png)

Editable source:
[`ci-release-gate.drawio`](diagrams/ci-release-gate.drawio).

## Definition of done

| Capability | Product behavior |
|---|---|
| Install | A wheel includes the scenario catalog, schema, and default release baseline. |
| Connect | AxonLLM and arbitrary OCI Agent-RPC adapters run multi-turn tool loops through one bounded session contract. |
| Contain | Every proposed tool action passes through the control plane; the actual Agent-RPC process and range effects can run in separate hardened gVisor workers. |
| Report | Each run preserves a manifest, hash-chained evidence, result, incident brief, and model metadata. |
| Gate | A versioned baseline evaluates containment, capability, evidence, validity, adapter identity, and exercised turns. |
| Integrate | GitHub Actions receives a non-zero exit for regression and uploads JSON, Markdown, HTML, JUnit, and run evidence. |
| Publish | When the repository is public and Pages is enabled, GitHub Pages presents the scenario table, diagrams, stable result JSON, and public incident replay; private repositories retain the same reviewable `docs/` source without attempting deployment. |
| Benchmark | A versioned private plan runs S02/S03/S06/S09 for 30 paired C1–C4 trials, rotates synthetic fixtures, enforces cost and runtime budgets, reports statistical and operational metrics, and emits an independent-review queue. |

## CI sequence

```text
AxonLLM pull request
        |
        v
Install AxonLLM + Escape Lab wheel
        |
        v
Run S03 / S06 / S09 through AxonLLM fixture routing
        |
        v
Intercept each synthetic tool call with C4 controls
        |
        v
Verify protected state, teardown, and evidence hash chain
        |
        v
Compare results with baselines/first-product.json
        |
        +---- pass ----> green release check
        |
        +-- regression -> exit 10 + evidence artifacts
```

Fixture mode proves the repository-to-router-to-tool-to-gate integration without
provider credentials or public network access. Live mode uses the same adapter
and enforcement path with operator-supplied AxonLLM model/provider
configuration.

Continuous CI builds the range image, runs the real OCI integration test, and
executes a self-contained S02 gate requiring the reference control backend and
Docker range backend. A second credential-free job installs a checksum-pinned
`runsc`, builds the generic Agent-RPC example image, runs it inside gVisor, and
evaluates the S03/S06/S09 external-agent containment baseline. Optional
authorized cross-repository jobs exercise private AxonLLM and Ostiari
integrations. The benchmark-readiness workflow also runs the full 30-trial
credential-free shadow campaign; its live-model job requires a protected
environment, explicit spend confirmation, private configuration, and named
approval.

## Remaining production boundary

This milestone isolates the complete synthetic range worker, the reviewed
offline AxonLLM fixture, and arbitrary OCI Agent-RPC processes. External images
start only after stopped-container attestation verifies gVisor syscall
mediation, zero egress, a read-only/no-mount filesystem, non-root execution,
zero capabilities, exact resource/swap limits, disabled container logging,
host-enforced turn/RPC bounds, and ID-bound teardown.

It does not claim T2 certification or provide direct credentialed remote-model
access. A reviewed inference broker, signed/attested images, optional micro-VM
execution, remote evidence retention, independent kill infrastructure,
external security assessment, and statistically calibrated live-model
qualification remain later product milestones.
