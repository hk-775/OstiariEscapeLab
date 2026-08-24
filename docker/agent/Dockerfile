# syntax=docker/dockerfile:1.7

FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS build

WORKDIR /build

# The named context intentionally copies only package inputs. It does not copy
# AxonLLM's .git directory, local environment files, artifacts, or credentials.
COPY --from=axonllm pyproject.toml README.md LICENSE THIRD_PARTY_NOTICES.md /build/AxonLLM/
COPY --from=axonllm axonllm /build/AxonLLM/axonllm
COPY --from=axonllm src /build/AxonLLM/src

COPY pyproject.toml README.md LICENSE MANIFEST.in /build/OstiariEscapeLab/
COPY src /build/OstiariEscapeLab/src

RUN python -m pip wheel \
      --disable-pip-version-check \
      --wheel-dir /wheels \
      /build/AxonLLM \
      /build/OstiariEscapeLab


FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

COPY --from=build /wheels /wheels

RUN python -m pip install \
      --disable-pip-version-check \
      --no-cache-dir \
      --no-index \
      --find-links=/wheels \
      axon-llm \
      ostiari-escape-lab \
    && rm -rf /wheels /root/.cache \
    && mkdir -p /range \
    && chown 65532:65532 /range

ENV HOME=/range \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONNOUSERSITE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /range
USER 65532:65532

ENTRYPOINT ["python"]
CMD ["-m", "escape_lab.agent_worker"]
