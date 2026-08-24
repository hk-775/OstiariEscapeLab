FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

COPY --chown=65532:65532 src/escape_lab /opt/escape-lab/escape_lab

RUN mkdir -p /range && chown 65532:65532 /range

ENV PYTHONPATH=/opt/escape-lab \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /range
USER 65532:65532

CMD ["python", "-m", "escape_lab.sandbox_worker"]
