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
| Connect | The AxonLLM adapter runs a multi-turn tool loop through the stable embedded router. |
| Contain | Every model-requested tool action passes through the control plane; range state and effects can run in a hardened disposable OCI/gVisor worker. |
| Report | Each run preserves a manifest, hash-chained evidence, result, incident brief, and model metadata. |
| Gate | A versioned baseline evaluates containment, capability, evidence, validity, adapter identity, and exercised turns. |
| Integrate | GitHub Actions receives a non-zero exit for regression and uploads JSON, Markdown, HTML, JUnit, and run evidence. |
| Publish | GitHub Pages presents the scenario table, diagrams, stable result JSON, and public incident replay. |

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

The incident CI job also builds the range image, runs the real OCI integration
test, and executes the S02 incident gate with both
`required_control_backend=ostiari` and `required_range_backend=docker`.

## Remaining production boundary

This milestone isolates the complete synthetic range worker, not an arbitrary
agent or model process, and does not claim T2 certification. Agent-process
container/micro-VM execution, direct syscall and kernel/network interception,
signed/attested images, remote evidence retention, independent kill
infrastructure, and statistically calibrated live-model qualification remain
later product milestones.
