# gVisor fixture-agent image

This image runs the real AxonLLM fixture loop inside a gVisor container. The
fixture provider is in-process: it opens no socket, requires no provider
configuration, and carries no API key or cloud identity.

Build from the Escape Lab repository root with the local AxonLLM checkout as a
restricted named build context:

```bash
docker build \
  --build-context axonllm=../AxonLLM \
  --file docker/agent/Dockerfile \
  --tag escape-lab-agent:dev \
  .
```

Run one isolated fixture scenario after registering `runsc` with Docker:

```bash
PYTHONPATH=src python3.12 -m escape_lab \
  --agent axonllm \
  --axonllm-mode fixture \
  --agent-runtime gvisor \
  --agent-image escape-lab-agent:dev \
  run S06 --profile C4
```

The host creates the image by immutable image ID with:

- `--runtime runsc`;
- `--network none` and `--ipc none`;
- no bind mounts, volumes, devices, environment file, or provider settings;
- a read-only root filesystem;
- all capabilities dropped and `no-new-privileges`;
- non-root UID/GID 65532;
- bounded CPU, memory, PIDs, file descriptors, and hardened tmpfs mounts.

Startup fails closed unless an in-boundary probe verifies those restrictions,
the provider transport is offline, and common provider/cloud identity
environment variables and files are absent. The host force-removes the exact
container on timeout or protocol failure and records verified teardown.

The container remains stopped until Docker's effective runtime, image, command,
network, namespace, mount, privilege, identity-input, and limit fields pass.
This qualifies the reviewed deterministic AxonLLM fixture process. Arbitrary
images use the same boundary through
[`ostiari-agent-rpc-v1`](../../docs/agent-rpc.md), but image provenance,
credentialed remote-provider access, hostile native code, and the underlying
runtime/kernel still require separate qualification.
