FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

COPY --chown=65532:65532 examples/agent-rpc/worker.py /opt/agent/worker.py

ENV HOME=/range \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /range
USER 65532:65532

ENTRYPOINT ["python", "/opt/agent/worker.py"]
