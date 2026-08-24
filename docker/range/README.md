# T2 range image

This image runs the Escape Lab range worker behind a JSON-line control channel.
The host-side orchestrator and Ostiari control plane remain outside the
container. Range state, previews, checkpoints, tool effects, and teardown run
inside the isolated boundary.

Build:

```bash
docker build \
  -f docker/range/Dockerfile \
  -t escape-lab-range:0.2.0 \
  .
```

Probe:

```bash
PYTHONPATH=src python3.12 -m escape_lab preflight \
  --docker-image escape-lab-range:0.2.0 \
  --probe-docker
```

Run one scenario with the Docker default runtime:

```bash
PYTHONPATH=src python3.12 -m escape_lab \
  --range-backend docker \
  --sandbox-image escape-lab-range:0.2.0 \
  run S02 --profile C4
```

Run it with gVisor after registering `runsc` as a Docker runtime:

```bash
PYTHONPATH=src python3.12 -m escape_lab \
  --range-backend gvisor \
  --sandbox-image escape-lab-range:0.2.0 \
  run S02 --profile C4
```

The worker starts only after an in-boundary probe confirms a non-root identity,
zero effective capabilities, `no-new-privileges`, a read-only root filesystem,
no usable non-loopback networking, and a writable bounded range tmpfs. The host
runs the container by immutable local image ID with pulling disabled.

The base image is pinned by digest. This is the first executable T2 boundary,
not an isolation certification and not yet an arbitrary-agent process runner.
Before multi-tenant use, generate an SBOM, sign the image, add reviewed
syscall/MAC policy, move execution to dedicated Linux workers, and independently
test runtime and kernel escape resistance.
