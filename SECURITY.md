# Security policy

Escape Lab is security-sensitive evaluation software. The latest release and
the `main` branch receive security fixes; older revisions are best effort.

## Report a vulnerability

Do not publish exploit details, credentials, sensitive logs, or an unpatched
proof of concept in a public issue.

Use the repository's private vulnerability-reporting form under the GitHub
Security tab. If that form is unavailable, open a public issue titled
`Security contact request` with no vulnerability details so a private channel
can be arranged.

Include:

- affected version or commit;
- expected and observed security boundary;
- impact and prerequisites;
- the smallest synthetic reproduction available;
- redacted logs or evidence references;
- any suggested mitigation.

Relevant reports include unintended host command execution, non-loopback
network access in fixture mode, a synthetic-range boundary bypass, secret
exposure, evidence-integrity failure, unsafe artifact deletion, or CI
supply-chain behavior that violates the documented design.

Keep testing confined to systems and repositories you are authorized to use.
The project does not authorize testing third-party providers, targets, or
infrastructure.
