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
adapter and never invokes a shell from scenario content.

## Authorization

Each scenario declares allowed actions, resources, destinations, identities,
time, and spending before execution. Every identity has
`"synthetic": true`. Preflight rejects host mounts, production credentials, and
non-deny network defaults.

C0 is not a normal execution mode. It is available only with an explicit flag
and remains inside the synthetic range.

## Evidence separation

The agent interacts only with the synthetic range. Evidence files live in a
separate artifact directory and are not exposed as a tool or state resource.
Every event is flushed and hash chained before the next action proceeds.

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

Synthetic ranges are created under one configured range root. Teardown refuses
to remove any path outside that root, clears synthetic processes, files,
network state, and identities, then removes the range directory.

For real T2 execution, the Docker posture adds:

- `--network none`;
- read-only root filesystem;
- all capabilities dropped;
- `no-new-privileges`;
- non-root UID;
- memory, CPU, and process limits;
- a bounded `noexec` temporary filesystem.

This posture is a starting point, not an isolation certification. Production T2
should add a reviewed seccomp/AppArmor profile, image signatures and digests,
separate control/execution/evidence accounts, runtime egress enforcement, and
independent kill infrastructure.

## Disclosure

Run evidence defaults to `private`. Post-run disclosure transitions are stored
in a separate append-only ledger using:

`private → acknowledged → remediated → coordinated → publishable`

The CLI permits explicit status entries but does not automatically publish or
contact third parties.

