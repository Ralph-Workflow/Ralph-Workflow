ARG PYTHON_VERSION=3.13

FROM debian:bookworm-slim AS uv-fetcher
ARG UV_VERSION=0.7.20
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends curl ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/* && \
    curl -fsSL https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-$(uname -m)-unknown-linux-gnu.tar.gz | \
    tar xzf - --strip-components=1 && chmod +x uv

FROM python:${PYTHON_VERSION}-slim AS builder

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends git ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=uv-fetcher /uv /usr/local/bin/uv

ENV UV_PYTHON_PREFERENCE=only-system

WORKDIR /build

COPY . .
RUN rm -f uv.lock .python-version && uv sync --no-dev --no-editable

FROM python:${PYTHON_VERSION}-slim

LABEL org.opencontainers.image.title="Ralph Workflow"
LABEL org.opencontainers.image.description="Vendor-neutral AI coding workflow orchestration with unattended execution, recovery, and verification."
LABEL org.opencontainers.image.url="https://ralphworkflow.com"
LABEL org.opencontainers.image.source="https://github.com/Ralph-Workflow/Ralph-Workflow"
LABEL org.opencontainers.image.vendor="RalphWorkflow"
LABEL org.opencontainers.image.licenses="AGPL-3.0-or-later"

RUN apt-get update -qq && \
    apt-get install -y -qq --no-install-recommends git ca-certificates && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

COPY --from=builder /build/.venv /opt/ralph/.venv

RUN rm -f /opt/ralph/.venv/bin/python /opt/ralph/.venv/bin/python3 && \
    ln -s /usr/local/bin/python3 /opt/ralph/.venv/bin/python && \
    ln -s /usr/local/bin/python3 /opt/ralph/.venv/bin/python3

RUN sed -i '1s|^#!/build/.venv/bin/python|#!/opt/ralph/.venv/bin/python|' \
      /opt/ralph/.venv/bin/ralph \
      /opt/ralph/.venv/bin/ralph-mcp 2>/dev/null || true

RUN ln -s /opt/ralph/.venv/bin/ralph /usr/local/bin/ralph && \
    ln -s /opt/ralph/.venv/bin/ralph-mcp /usr/local/bin/ralph-mcp

WORKDIR /workspace

RUN ralph --version

ENTRYPOINT ["ralph"]
CMD ["--help"]
