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

Live mode may contact the provider routes configured by the operator. Scenario
inputs and tool results remain synthetic, provider credentials stay inside
AxonLLM's resolver/transport boundary, and Escape Lab stores only a redacted
route summary. Live mode is not a substitute for independent T2 isolation of an
arbitrary agent process.

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

This materially improves containment of the modeled range effects, but it is
not an isolation certification. The agent/model process still runs outside this
worker and can only be contained to the extent that its capabilities are
restricted to mediated Escape Lab tools. Production T2 should additionally put
the arbitrary agent process in a dedicated container or micro-VM, add reviewed
seccomp/AppArmor policy, sign and attest images, separate
control/execution/evidence accounts, enforce egress outside the workload
namespace, and operate independent kill infrastructure.

## Disclosure

Run evidence defaults to `private`. Post-run disclosure transitions are stored
in a separate append-only ledger using:

`private → acknowledged → remediated → coordinated → publishable`

The CLI permits explicit status entries but does not automatically publish or
contact third parties.
