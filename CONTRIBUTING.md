# Contributing

Escape Lab welcomes focused fixes, documentation improvements, new controls,
and carefully reviewed synthetic scenarios.

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

make lint
make test
```

To exercise the real AxonLLM integration through the loopback-only fixture:

```bash
python -m pip install -e ../AxonLLM
AXONLLM_SRC=../AxonLLM make test
```

## Pull requests

- Keep changes scoped and explain the product or safety behavior they change.
- Add or update tests for runtime, policy, evidence, packaging, and CLI changes.
- Update requirements traceability when a specification requirement changes
  status.
- Update `CHANGELOG.md` for user-visible changes.
- Preserve the deny-by-default network boundary and synthetic-only fixtures.
- Do not add real credentials, personal data, live targets, exploit payloads,
  or commands that the range would execute on a host.

The canonical reviewed data lives under `baselines/`, `scenarios/`, and
`schemas/`. After editing it, refresh the wheel copies and verify them:

```bash
make sync-data
make check-data
```

Editable diagrams belong in `docs/diagrams/*.drawio`; commit the matching PNG
export whenever the diagram changes.

## Scenario contributions

A scenario must define explicit authority, synthetic identities and state,
deny-by-default networking, deterministic outcome assertions, budgets,
instrumentation requirements, and teardown checks. It must remain useful for
defensive containment evaluation without materially increasing offensive
capability.

Security-sensitive findings should follow `SECURITY.md`, not a public issue.
