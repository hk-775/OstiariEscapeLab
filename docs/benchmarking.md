# Private benchmark pilot

Escape Lab includes a versioned private benchmark campaign for one
AxonLLM-routed live model:

- scenarios S02, S03, S06 and S09;
- profiles C1 through C4;
- 30 paired trials per scenario/profile;
- 480 total runs;
- seeded per-trial canary and fixture variants;
- the real Ostiari control backend;
- an isolated Docker range for tool effects;
- explicit token, action, retry, delegation, time and cost budgets;
- statistical, capability, evidence, recovery, latency and cost thresholds;
- an independent-review queue and hash-chained reviewer ledger.

The reviewed plan is
[`benchmarks/private-pilot-v0.1.json`](../benchmarks/private-pilot-v0.1.json).
Its schema is
[`schemas/benchmark-plan.schema.json`](../schemas/benchmark-plan.schema.json).

## Safety boundary

The private pilot treats the remote model as a tool-calling service. AxonLLM
and the provider credential remain in the host control plane. The model sees
only the scenario prompt, synthetic tool definitions and mediated tool
observations. All modeled process, filesystem and network effects execute in
the disposable Docker range.

Provider configuration must reference a credential environment variable.
Never embed a provider key in a model, provider or pricing file. Configuration
files are represented in evidence only by digests and credential-free route
metadata.

This is a private model-behavior and control-stack benchmark. It does not place
a credentialed provider client inside gVisor and does not certify the Docker
host, kernel or provider.

## Validate without provider calls

```bash
escape-lab benchmark \
  --plan benchmarks/private-pilot-v0.1.json \
  --validate-only
```

The command reports 480 planned runs and a `$120.00` campaign budget envelope
derived from the per-run budget. Escape Lab checks accounted cost after each
model response and terminates a run that crosses its budget. Because provider
billing is retrospective, use a provider-side account/project cap for a true
financial ceiling and reduce the plan budget before execution when appropriate.

The credential-free shadow plan exercises the complete campaign scheduler,
variant generator, metrics, thresholds, reports and review queue:

```bash
escape-lab \
  --artifacts-root artifacts/benchmark-shadow \
  benchmark \
  --plan benchmarks/private-pilot-shadow-v0.1.json \
  --output-dir artifacts/benchmark-shadow/report
```

Shadow results validate benchmark plumbing only; they are not live-model
measurements.

## Run the private live campaign

Build the isolated range:

```bash
docker build \
  --file docker/range/Dockerfile \
  --tag escape-lab-range:benchmark \
  .
```

Then execute with reviewed AxonLLM, Ostiari and provider configuration:

```bash
escape-lab \
  --artifacts-root artifacts/private-live-benchmark \
  --axonllm-src ../AxonLLM \
  --axonllm-models /secure/config/models.yaml \
  --axonllm-providers /secure/config/providers.yaml \
  --axonllm-pricing /secure/config/pricing.yaml \
  --ostiari-src ../ostiari \
  --model logical-model-name \
  --provider provider-name \
  --sandbox-image escape-lab-range:benchmark \
  benchmark \
  --plan benchmarks/private-pilot-v0.1.json \
  --confirm-live-spend \
  --output-dir artifacts/private-live-benchmark/report
```

The plan, rather than command-line defaults, selects live AxonLLM, the Ostiari
backend, Docker range, C1–C4 profiles, sampling parameters and budgets. Live
execution fails closed when pricing cannot account for a model turn.

## Output

```text
artifacts/private-live-benchmark/
├── runs/...
├── experiments/...
└── report/
    ├── benchmark.json
    ├── benchmark.md
    ├── benchmark.html
    ├── junit.xml
    └── review-queue.json
```

The aggregate report includes containment failure rate and Wilson interval,
risk-weighted loss, attempt and prevention rates, false-block rate, residual
task capability, recovery and evidence completeness, approval burden, time and
actions to intervention, run duration, decision latency, cost, tokens, retries
and C1-relative overhead.

## Independent review

Every O2–O4 run and a deterministic 10% sample of O0–O1 runs require two
independent reviews. Outcome disagreement requires a third reviewer.

```bash
escape-lab review-add artifacts/private-live-benchmark/report RUN_ID \
  --reviewer reviewer-identity \
  --outcome O1 \
  --severity 0 \
  --attribution agent \
  --confidence 0.9 \
  --note "Reviewed observable events and protected-state snapshots."

escape-lab review-status artifacts/private-live-benchmark/report
escape-lab review-finalize artifacts/private-live-benchmark/report
```

Finalization writes `benchmark-final.json` and `adjudication.md`. Results remain
provisional until the review queue is complete, the ledger verifies, and any
third-review disagreement is resolved.

## GitHub Actions

The `Benchmark readiness` workflow continuously runs the 30-trial shadow
campaign. Its protected live job runs only when:

1. workflow dispatch selects `run_live`;
2. `ENABLE_PRIVATE_BENCHMARK=true`;
3. the `private-benchmark` environment approves the job; and
4. the required private configuration and credential secrets exist.

Configure:

| Type | Name |
|---|---|
| Variable | `ENABLE_PRIVATE_BENCHMARK` |
| Variable | `BENCHMARK_MODEL` |
| Variable | `BENCHMARK_PROVIDER` |
| Variable | `BENCHMARK_PROVIDER_ENV_NAME` |
| Secret | `CROSS_REPO_TOKEN` |
| Secret | `BENCHMARK_MODELS_YAML` |
| Secret | `BENCHMARK_PROVIDERS_YAML` |
| Secret | `BENCHMARK_PRICING_YAML` |
| Secret | `BENCHMARK_PROVIDER_CREDENTIAL` |

Require named reviewers on the `private-benchmark` environment before enabling
the live job. The workflow retains private live evidence for 90 days.
