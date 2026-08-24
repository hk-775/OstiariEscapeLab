# External Agent-RPC protocol

Ostiari Escape Lab can run an arbitrary OCI-contained agent under gVisor when
the image implements `ostiari-agent-rpc-v1`. The protocol is newline-delimited
JSON over standard input and standard output. Each record is limited to 2 MiB,
requests are processed serially, and responses must echo the request `id`.
Diagnostic logs belong on standard error; standard output is reserved for RPC.

## Launch contract

Use `--agent external --agent-runtime gvisor --agent-image IMAGE`. By default,
Escape Lab uses the image's JSON-form `ENTRYPOINT` and `CMD`. To override them
without a host shell, pass a JSON argv:

```bash
escape-lab \
  --agent external \
  --agent-runtime gvisor \
  --agent-image my-agent:reviewed \
  --agent-command-json '["python","/opt/agent/worker.py"]' \
  run S06 --profile C4
```

The host resolves the image to its immutable local image ID, creates a stopped
container, inspects Docker's effective configuration, and starts it only after
every policy check passes. The attested boundary requires:

- `runsc`, which places application syscalls behind the gVisor Sentry;
- `--network none`, no published ports, and no host networking;
- a read-only root, no binds or volumes, and hardened bounded tmpfs mounts at
  `/range` and `/tmp`;
- UID/GID 65532, zero capabilities, `no-new-privileges`, no devices, private
  namespaces, disabled restart and healthcheck behavior, and CPU/memory/PID/
  file-descriptor limits;
- memory swap limited to the configured memory ceiling and Docker container
  logging disabled;
- no forwarded host environment and no recognized provider/cloud identity
  variable.

The host, rather than the untrusted worker, enforces the maximum turn count.
RPC records, response backlog, and diagnostic chunks are bounded. Cleanup is
bound to the exact container ID returned by the successful `docker create`, so
a failed name collision cannot remove a pre-existing container.

Image contents are still part of the trusted supply chain. Escape Lab prevents
host credential injection but cannot prove that an arbitrary image was not
built with a credential already embedded in a layer.

## Requests

Every request has this envelope:

```json
{"id": 1, "operation": "init", "payload": {}}
```

Every response has one of these forms:

```json
{"id": 1, "ok": true, "result": {}}
{"id": 1, "ok": false, "error": {"type": "ValueError", "message": "..."}}
```

The host sends four operations:

| Operation | Purpose | Required result |
|---|---|---|
| `init` | Supply the resolved scenario, digest, seed, run ID, turn budget, and protocol name. | Object containing `agent_metadata`. |
| `next_event` | Ask the agent for its next mediated action or completion. | Encoded `action`, `skip`, or `completed` event. |
| `observe` | Return the control decision and optional tool result for the preceding action. | Any JSON value; `{"observed": true}` is conventional. |
| `shutdown` | Request graceful termination before verified container removal. | Any JSON value, after which the process exits. |

An `init` result may also include `isolation_probe` and `identity_probe`
objects with `"passed": true`. They are required for the reviewed AxonLLM
fixture. For arbitrary images they are optional defense-in-depth signals;
Docker's stopped-container attestation remains authoritative.

## Events

An action response uses:

```json
{
  "type": "action",
  "event": {
    "step_id": "publish_raw",
    "actor": "agent",
    "action": "network.send",
    "params": {"destination": "review.mock", "payload": {"summary": "..."}},
    "description": "Send a reviewed summary",
    "expected_authorized": true,
    "prohibited_attempt": false,
    "save_as": null,
    "parent_step_id": null,
    "tool_call_id": "optional-provider-tool-id",
    "usage": {
      "prompt_tokens": 0,
      "completion_tokens": 0,
      "total_tokens": 0,
      "model_turns": 0,
      "provider": null,
      "model": null
    }
  }
}
```

All actions are proposals. The host control plane decides whether to allow,
deny, redact, approve, suspend, or terminate them before any modeled range
effect executes.

See [`examples/agent-rpc`](../examples/agent-rpc/) for a complete worker and
Dockerfile.

## Live models

A local model bundled into the image can use this protocol immediately.
Credentialed remote providers are intentionally not reachable from the agent
container: direct internet access or injected API keys would defeat the
zero-egress boundary. Remote inference requires a separately reviewed,
allowlisted broker that keeps credentials outside the workload; that broker is
not part of `ostiari-agent-rpc-v1`.
