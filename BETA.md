# Public beta

Ostiari Escape Lab `0.3.0b1` is the first public beta of the defensive
agent-containment benchmark and CI release gate.

## Supported beta scope

- Installable Python 3.10–3.12 wheel and source distribution.
- Deterministic S01–S12 scenario and C0–C4 control-profile evaluation.
- JSON, Markdown, HTML, JUnit, evidence-chain, replay, and disclosure outputs.
- Docker-isolated range execution on reviewed Linux hosts.
- Arbitrary `ostiari-agent-rpc-v1` OCI workers under a required gVisor
  `runsc` runtime with zero network and zero injected identity.
- Credential-free AxonLLM fixture integration and optional reviewed AxonLLM and
  Ostiari source integrations.
- CI regression gates and the credential-free 480-run benchmark shadow
  campaign.

The continuous release gates run on Ubuntu 24.04. Core deterministic commands
are portable Python; Docker and gVisor features require a compatible Linux
host.

## Install

Install the wheel attached to the beta release:

```bash
python -m pip install \
  https://github.com/hk-775/OstiariEscapeLab/releases/download/v0.3.0b1/ostiari_escape_lab-0.3.0b1-py3-none-any.whl
```

Then validate the packaged catalog and run the synthetic demo:

```bash
escape-lab validate
escape-lab demo
```

The release also includes a source distribution, SHA-256 checksums, and a
GitHub build-provenance attestation. PyPI publication is intentionally deferred
until a project-scoped Trusted Publisher is configured.

## Known limitations

This beta is not a production containment certification. In particular:

- remote provider credentials remain in the host-side AxonLLM control plane;
- there is no reviewed identity-isolating inference broker;
- images are pinned at execution but are not yet signed or policy-attested;
- evidence retention and the kill file are local rather than independent
  production services;
- syscall activity is mediated by gVisor but is not translated into semantic
  action evidence;
- micro-VM execution, multi-tenant worker pools, load qualification, and an
  external runtime/kernel security assessment remain pending;
- published model results are deterministic fixtures and shadow campaigns, not
  completed live-model statistical qualification.

See
[`docs/requirements-traceability.md`](docs/requirements-traceability.md) and
[`docs/security-safety.md`](docs/security-safety.md) for the detailed boundary.

## Feedback

Use GitHub issues for reproducible bugs, documentation gaps, compatibility
reports, and feature proposals. Do not put vulnerability details, credentials,
customer data, or unpatched proofs of concept in a public issue; follow
[`SECURITY.md`](SECURITY.md) instead.

Beta feedback should include the release version, Python version, host
platform, selected range and agent runtimes, and a redacted evidence reference.
