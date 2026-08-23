# T2 range image

This minimal image exists to exercise the container security posture emitted by
`escape-lab preflight`. It does not yet run arbitrary agents.

Build:

```bash
docker build -t escape-lab-range:0.1.0 docker/range
```

Probe:

```bash
PYTHONPATH=src python3.12 -m escape_lab preflight \
  --docker-image escape-lab-range:0.1.0 \
  --probe-docker
```

The runtime command uses a read-only root, no network, no capabilities,
`no-new-privileges`, a non-root UID, resource limits, and a bounded `noexec`
temporary filesystem.

The base image is pinned by digest. Before using this as a real T2 range,
generate an SBOM, sign the image, add reviewed syscall/MAC policy, and keep the
control and evidence planes outside the container boundary.
