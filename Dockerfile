# fief-relay hub image — shared by Render, Fly, Pi, any Docker host.
# Single Dockerfile for all arches: the chisel binary is fetched at runtime
# per-arch (amd64/arm64/...) by `fief hub`, so no TARGETARCH tricks needed.
# Runs as root so the optional sshd sidecar (SSH_PUBKEY) can start;
# without SSH_PUBKEY the hub stays chisel-only. No secrets baked in.
FROM docker.io/library/python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PORT=8080

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /uvx /bin/

# sshd sidecar deps + `fief` user (key-only login target over the tunnel).
# hadolint ignore=DL3008: base tracks debian-slim; a pinned openssh
# version string would rot on every base update.
RUN apt-get update \
    && apt-get install -y --no-install-recommends openssh-server \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/sh fief \
    && mkdir -p /run/sshd /home/fief/.ssh \
    && chmod 700 /home/fief/.ssh \
    && chown fief:fief /home/fief/.ssh

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
RUN uv sync --frozen --no-dev --no-editable
ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8080

CMD ["fief", "hub"]
