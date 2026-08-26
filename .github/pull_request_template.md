## Summary

Describe the product, safety, or documentation behavior changed by this pull
request.

## Validation

- [ ] Tests cover the changed behavior.
- [ ] `make lint` passes.
- [ ] `make test` passes.
- [ ] Packaged data is synchronized when canonical JSON changed.
- [ ] Requirements traceability and the changelog are updated when applicable.

## Safety

- [ ] No production credentials, personal data, live targets, or operational
      exploit payloads are included.
- [ ] Network, identity, filesystem, evidence, and teardown boundaries are no
      weaker than before, or the change clearly documents why.
- [ ] Security-sensitive details are being handled privately under
      `SECURITY.md`.
