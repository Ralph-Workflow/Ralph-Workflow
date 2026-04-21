# Ralph Workflow (Python)

Ralph Workflow is a Python 3.12+ CLI for unattended multi-agent development loops. The installable package lives in this directory and exposes two entry points:

- `ralph` — the main CLI
- `ralph-mcp` — the standalone MCP server runtime

## Install

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

### pipx

```bash
python -m pip install pipx
python -m pipx ensurepath
pipx install ralph-workflow
ralph --help
```

### Development install

```bash
make dev
ralph --version
```

### Refresh the runnable executable from this checkout

```bash
make install
ralph --version
```

`make install` updates the current Python environment and, when `pipx` is
available, force-refreshes the pipx-managed `ralph` executable so it points at
the current checkout instead of a stale deleted worktree.

## Quick start

```bash
cd /path/to/your/project
ralph --init feature-spec
# edit PROMPT.md
ralph
```

## Verification

```bash
make verify
```

That runs:

- `ruff check ralph/ tests/`
- `uv run python -m mypy ralph/`
- `uv run python -m ralph.verify_timeout --suite-timeout 30 -- pytest tests/ -q -n 8 --cov=ralph --cov-report=term-missing --cov-report=html --cov-fail-under=80`

For narrower local runs, use:

- `make test` — full pytest suite without coverage
- `make test-unit` — everything under `tests/` except `tests/integration/`
- `make test-integration` — `tests/integration/` only

For the dead-code policy tooling, run the separate Vulture audit:

```bash
make dead-code
```

It is intentionally separate from `make verify` while the current dead-code backlog still exists; today the command proves the scanner wiring by failing on the code that still needs cleanup.

## Package map

- `ralph/cli/` — Typer CLI entry points and command plumbing
- `ralph/config/` — layered config loading and Pydantic models
- `ralph/pipeline/` — state, events, reducer, orchestrator, effects
- `ralph/phases/` — phase handlers and dispatch
- `ralph/agents/` — agent registry, chains, and invocation
- `ralph/mcp/` — MCP bridge, artifact handling, standalone server runtime
- `ralph/git/` — GitPython-backed repository helpers and rebase support
- `ralph/workspace/` — production and in-memory filesystem abstractions

## Pydoc-first API reference

The public package docstrings are intended to stand on their own. Useful entry points:

```bash
python -m pydoc ralph
python -m pydoc ralph.cli
python -m pydoc ralph.pipeline
python -m pydoc ralph.mcp
python -m pydoc ralph.git
python -m pydoc ralph.workspace
```

Use package/module docstrings for API understanding and this README for workflow-level guidance.

## Phase-output hardening

Ralph now treats several agent-driven phases as producing explicit evidence, not just a zero exit code.

- `development` must leave behind a fresh `.agent/artifacts/development_result.json`.
- `review` must leave behind a fresh `.agent/artifacts/issues.json`.
- `fix` must leave behind a fresh `.agent/artifacts/fix_result.json`.
- Planning keeps `.agent/artifacts/plan.json` as the canonical machine-readable artifact and mirrors it to `.agent/PLAN.md` as the human/agent handoff.
- The runner removes those per-phase artifacts before each invocation so a later interrupted run cannot silently reuse stale output from an earlier pass.

Artifact contract:
- Use `.json` artifacts for Ralph's validation, routing, checkpointing, and other orchestrator-only logic.
- Use `.md` handoff files when a user or downstream AI agent needs to read the result of an earlier phase.
- Current mirrored handoffs are:
  - `.agent/PLAN.md`
  - `.agent/DEVELOPMENT_RESULT.md`
  - `.agent/ISSUES.md`
  - `.agent/FIX_RESULT.md`
  - `.agent/DEVELOPMENT_ANALYSIS_DECISION.md`
  - `.agent/REVIEW_ANALYSIS_DECISION.md`

This hardening is intentionally strict. It adds complexity, but it closes a real unattended-mode failure class where a provider could exit successfully, emit no meaningful work, and still let the pipeline advance because an old artifact was still present on disk.

## Multimodal MCP Support (opt-in)

Ralph supports image-reading MCP tools via the `read_image` tool. This feature is **disabled by default** to maintain backward compatibility with text-only clients.

### Enabling Multimodal Support

Add a `[media]` section to `.agent/mcp.toml`:

```toml
[media]
enabled = true
max_inline_bytes = 5242880  # 5 MiB, default
```

### How read_image Works

When enabled, `read_image` is available with these characteristics:

- **Supported formats**: PNG, JPEG, GIF, WebP
- **Returns**: MCP image content block with base64-encoded data
- **Size guard**: Enforces `max_inline_bytes` limit (default 5 MiB)
- **Capability required**: `MediaRead` (granted by session policy)

### Compatibility Contract

The multimodal support is designed with strict backward compatibility:

1. **Text-only clients unchanged** — Existing tools (`read_file`, `write_file`, etc.) continue to return text content blocks with the same shape.
2. **Client capability filtering** — `read_image` only appears in `tools/list` for clients that declare multimodal/image/media capability in the MCP `initialize` handshake.
3. **Upstream multimodal rejection** — If an upstream MCP server returns a non-text content block, Ralph rejects it with a clear error rather than silently passing it through.

### What Text-Only Clients See

When a client connects without declaring multimodal support, `read_image` is **not visible** in `tools/list`, even if `media.enabled = true`. The text-only tool set is byte-equivalent to pre-multimodal behavior.

### What Multimodal Clients See

Clients that declare `capabilities.image`, `capabilities.media`, or `capabilities.multimodal` in the `initialize` request will see `read_image` in `tools/list` when `media.enabled = true`.

## Claude/CCS MCP Safety Note

Claude-compatible transports such as `claude` and `ccs` run through a stricter MCP path. Ralph still uses `--mcp-config` plus `--strict-mcp-config`, but it only emits `--tools ""` / `--allowedTools ...` when live MCP tool discovery succeeds with a non-empty allowlist. That avoids a brittle edge case in non-interactive Claude/CCS runs where empty-tool configurations and MCP bootstrapping can produce misleadingly successful no-op executions.

## Parallel Mode

When your planning phase produces two or more work units, Ralph fans development out across multiple git worktrees simultaneously. Configure it in your pipeline policy:

```toml
[pipeline.parallel_execution]
max_parallel_workers = 4
max_work_units = 50
```

See `docs/agents/parallelization.md` for the full guide.
