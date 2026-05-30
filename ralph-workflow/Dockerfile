# ── Ralph Workflow Docker image ──────────────────────────────────────────
# Multi-stage build that produces a minimal runtime image.
# Ralph Workflow is a Python CLI that shelves out to AI coding agents and
# uses git for repo ops.
#
# Usage:
#   docker build -t ralph-workflow .
#   docker run --rm -it -v "$(pwd):/workspace" -v "$HOME/.ralph:/root/.ralph" ralph-workflow ralph --help

ARG PYTHON_VERSION=3.13

# ── Stage 1: uv binary fetcher ──────────────────────────────────────────
FROM debian:bookworm-slim AS uv-fetcher
ARG UV_VERSION=0.7.20
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    curl -fsSL https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-$(uname -m)-unknown-linux-gnu.tar.gz | \
    tar xzf - --strip-components=1 && chmod +x uv

# ── Stage 2: builder ─────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends git ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=uv-fetcher /uv /usr/local/bin/uv

# Use system Python (3.13) — uv.lock is pinned to managed Python 3.14
ENV UV_PYTHON_PREFERENCE=only-system

WORKDIR /build

# Copy everything except .dockerignore'd files, resolve, install non-editable
COPY . .
RUN rm -f uv.lock .python-version && uv sync --no-dev --no-editable

# ── Stage 3: runtime ─────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim

LABEL org.opencontainers.image.title="Ralph Workflow"
LABEL org.opencontainers.image.description="Vendor-neutral AI coding workflow orchestration with unattended execution, recovery, and verification."
LABEL org.opencontainers.image.url="https://ralphworkflow.com"
LABEL org.opencontainers.image.source="https://codeberg.org/RalphWorkflow/Ralph-Workflow"
LABEL org.opencontainers.image.vendor="RalphWorkflow"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends git ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/.venv /opt/ralph/.venv

# Relink venv Python to system Python
RUN rm -f /opt/ralph/.venv/bin/python /opt/ralph/.venv/bin/python3 && \
    ln -s /usr/local/bin/python3 /opt/ralph/.venv/bin/python && \
    ln -s /usr/local/bin/python3 /opt/ralph/.venv/bin/python3

# Fix shebangs that reference the builder path
RUN sed -i '1s|^#!/build/.venv/bin/python|#!/opt/ralph/.venv/bin/python|' \
      /opt/ralph/.venv/bin/ralph \
      /opt/ralph/.venv/bin/ralph-mcp \
      /opt/ralph/.venv/bin/ralph-prompt 2>/dev/null || true

# Symlink entrypoints into PATH
RUN ln -s /opt/ralph/.venv/bin/ralph /usr/local/bin/ralph && \
    ln -s /opt/ralph/.venv/bin/ralph-mcp /usr/local/bin/ralph-mcp && \
    ln -s /opt/ralph/.venv/bin/ralph-prompt /usr/local/bin/ralph-prompt

WORKDIR /workspace

RUN ralph --version

ENTRYPOINT ["ralph"]
CMD ["--help"]
