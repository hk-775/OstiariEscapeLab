# Changelog

All notable user-visible changes are recorded here.

## Unreleased

- Added the generic `external-agent-rpc` adapter, documented
  `ostiari-agent-rpc-v1`, and a dependency-free example image for arbitrary
  local agent/model processes.
- Changed the gVisor agent lifecycle to create, inspect, and attest the stopped
  container before execution. The contract now records explicit syscall,
  filesystem, egress, and identity enforcement domains.
- Bounded host-side response and diagnostic buffering, disabled Docker
  container logging, enforced turn and swap limits outside the worker, and
  bound teardown to the exact created container ID.
- Added a continuous credential-free GitHub Actions gate that
  checksum-verifies and installs pinned gVisor, runs the external Agent-RPC
  image under `runsc`, evaluates a self-contained containment baseline, and
  uploads evidence. Private AxonLLM qualification remains an optional
  authorized integration.
- Made the results-dashboard workflow skip cleanly while the repository is
  private and resume GitHub Pages deployment when it is public and enabled.
- Added a credential-free, socket-free AxonLLM fixture transport and a dedicated
  gVisor agent worker with bounded RPC, identity-absence probes, immutable image
  resolution, fail-closed runtime checks, and verified teardown.
- Added an agent-runtime release-gate requirement, isolated-fixture baseline,
  Docker image, CLI configuration, and real-runtime integration test.
- Added dual in-boundary/Docker-contract attestation and a published
  three-scenario runsc qualification result with zero agent network,
  credentials, mounts, devices, or published ports.
- Added a pluggable range-session interface and executable hardened
  Docker/gVisor worker for range state and modeled tool effects.
- Added immutable local image resolution, runtime availability checks,
  in-boundary isolation verification, RPC timeouts, forced removal, and
  teardown evidence.
- Added release-gate requirements for control and range backends, real OCI
  integration tests, and an OCI-backed incident regression in CI.
- Updated the architecture diagram, safety case, product boundary, and
  requirements traceability for arbitrary Agent-RPC process isolation and the
  remaining credentialed-inference and independent-certification boundary.

## 0.2.0 - 2026-08-23

First product milestone:

- Added installable package data for scenarios, schema, and release baseline.
- Added the AxonLLM live adapter and loopback-only CI fixture.
- Added containment release gates with JSON, Markdown, HTML, and JUnit output.
- Added GitHub Actions integration for Escape Lab and AxonLLM changes.
- Added model-turn and token evidence, lifecycle hardening, and CI diagrams.
- Added contributor, security, packaging-sync, and open-source release assets.
- Added a publishable synthetic replay of the July 2026 OpenAI/Hugging Face
  agent incident, including Ostiari boundary/pivot decisions and gate evidence.
- Added a GitHub Pages results dashboard with scenario tables and editable
  Draw.io/PNG visuals.

## 0.1.0 - 2026-08-23

- Added the initial twelve-scenario synthetic benchmark.
- Added C0-C4 controls, evidence, replay, disclosure, reports, and Docker
  hardening guidance.
