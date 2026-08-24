# Security and safety case

## Hard boundaries

The repository and generated evidence must not contain:

- production credentials or identities;
- personal or customer data;
- uncontrolled public internet access;
- live third-party systems or accounts;
- undisclosed exploit code;
- operational instructions that materially increase offensive capability.

The default runner enforces these boundaries structurally: it has no network
adapter and never invokes a shell from scenario content. The isolated backend
adds an executable OCI/gVisor boundary for range state and modeled tool effects.

## Agent integration boundary

The AxonLLM adapter offers only tool schemas derived from the selected synthetic
scenario. A model cannot receive a shell, host filesystem, production identity,
or direct network tool through this adapter.

Fixture mode starts a provider stub on `127.0.0.1` and configures AxonLLM to use
only that route. It requires no provider credential and makes no public network
request. Its purpose is to prove that routing and tool-call translation remain
connected to containment CI.

The isolated fixture mode removes even that HTTP dependency. AxonLLM and its
deterministic provider execute inside gVisor using an in-process transport. The
container receives no provider configuration, provider/cloud identity
environment variable, credential file, host mount, or usable non-loopback
network route. Startup fails closed unless the in-boundary isolation and
identity-absence probes pass.

External mode accepts any reviewed OCI image that implements
`ostiari-agent-rpc-v1`. The host creates the container in a stopped state and
verifies Docker's effective image, command, runtime, namespaces, filesystem,
network, privilege, identity-input, healthcheck/restart, and resource-limit
configuration before starting the agent process. The host contract is
authoritative; self-attestation from an arbitrary image is optional and is
never used to weaken a failed host check.

Live mode may contact the provider routes configured by the operator. Scenario
inputs and tool results remain synthetic, provider credentials stay inside
AxonLLM's resolver/transport boundary, and Escape Lab stores only a redacted
route summary. This credentialed host-side mode is distinct from the external
gVisor boundary. A local model can run inside the external image, but a remote
provider requires a future separately reviewed broker; the isolated agent is
never given direct internet access or a provider key.

## Authorization

Each scenario declares allowed actions, resources, destinations, identities,
time, and spending before execution. Every identity has
`"synthetic": true`. Preflight rejects host mounts, production credentials, and
non-deny network defaults.

C0 is not a normal execution mode. It is available only with an explicit flag
and remains inside the synthetic range.

## Evidence separation

The agent adapter interacts only through the range protocol. Evidence files
live in a separate host artifact directory and are not mounted into, exposed as
a tool to, or writable by the isolated range worker. Every event is flushed and
hash chained before the next action proceeds.

S12 models loss of an in-band observation channel while retaining enough
out-of-band evidence to adjudicate the agent-caused suppression.

## Approval binding

C4 approvals are bound to:

- run and trajectory;
- parent action;
- actor and tool;
- complete parameter digest;
- effective resource and destination;
- expiry.

Changing any field invalidates the HMAC. The reference auto-approver does not
approve high-risk or prohibited effects.

## Teardown

In-process synthetic ranges are created under one configured range root.
Teardown refuses to remove any path outside that root, clears synthetic
processes, files, network state, and identities, then removes the range
directory.

For the executable isolated backend, the host resolves the requested local
image to its immutable image ID, disables pulling, and starts one named worker
per run with:

- `--network none`;
- `--ipc none`;
- read-only root filesystem;
- all capabilities dropped;
- `no-new-privileges`;
- non-root UID;
- memory, CPU, process, and file-descriptor limits;
- bounded `noexec`, `nosuid`, `nodev` tmpfs mounts for `/tmp` and `/range`.

The worker starts only if an in-boundary probe confirms the non-root identity,
zero effective capabilities, `no-new-privileges`, read-only root, no usable
non-loopback networking, and the writable bounded range tmpfs. RPC timeouts
force-remove the exact named container. Normal teardown records the final
snapshot, shuts down the worker, and verifies that the container no longer
exists.

This materially improves containment of modeled range effects, but the range
worker alone is not an agent-process boundary. Select the external gVisor agent
runtime when the actual agent/model process must be isolated as well.

For both the fixture and external agent runtime, Escape Lab first creates a
stopped container and inspects Docker's actual record. Execution is refused
unless `runsc`, `NetworkMode=none`, private IPC/PID/UTS/cgroup namespaces, a
read-only root, no host mounts, hardened tmpfs options, non-root identity,
dropped capabilities, no-new-privileges, no devices or published ports,
disabled restart/healthcheck behavior, no recognized identity environment
input, disabled container logging, and exact resource limits including swap
all pass. Only then does the host attach and start the Agent-RPC process. The
host also enforces the turn budget, bounds RPC and diagnostic buffering, and
removes by the created container ID rather than by a reusable name.

The reviewed AxonLLM fixture also performs in-boundary isolation and
identity-absence probes. An arbitrary worker may report equivalent probes, but
its claims are defense-in-depth only. `runsc` mediates application syscalls
through the gVisor Sentry; Escape Lab does not semantically approve individual
syscalls or independently certify `runsc`, Docker, or the host kernel.

Image contents remain a supply-chain trust input. No host credential is
injected, but an image built with an embedded secret cannot be proven clean by
runtime configuration alone. Production T2 should sign and attest images, use
dedicated Linux workers or micro-VMs where warranted, separate
control/execution/evidence accounts, operate independent kill infrastructure,
and commission an external isolation assessment.

## Disclosure

Run evidence defaults to `private`. Post-run disclosure transitions are stored
in a separate append-only ledger using:

`private → acknowledged → remediated → coordinated → publishable`

The CLI permits explicit status entries but does not automatically publish or
contact third parties.
