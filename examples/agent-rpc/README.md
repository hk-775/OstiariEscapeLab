# External Agent-RPC example

This dependency-free worker demonstrates the protocol used to run an arbitrary
agent process inside Ostiari Escape Lab's gVisor boundary. It is intentionally
deterministic; replace its `ExampleAgent` logic with a local model or framework
adapter while preserving the stdin/stdout protocol.

Build it from the repository root:

```bash
docker build \
  --file examples/agent-rpc/Dockerfile \
  --tag escape-lab-external-agent:dev \
  .
```

Run it after registering `runsc` with Docker:

```bash
escape-lab \
  --agent external \
  --agent-runtime gvisor \
  --agent-image escape-lab-external-agent:dev \
  run S06 --profile C4
```

The image receives the scenario only through Agent-RPC. It receives no host
mount, provider credential, Docker socket, device, or network interface.
See [`docs/agent-rpc.md`](../../docs/agent-rpc.md) for the protocol contract.
