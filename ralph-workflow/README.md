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

- `review` must leave behind a fresh `.agent/artifacts/issues.json`.
- `development` and `fix` are side-effect-driven phases: Ralph judges them by the workspace changes they make, not by whether they submit a structured result artifact.
- Planning keeps `.agent/artifacts/plan.json` as the canonical machine-readable artifact and mirrors it to `.agent/PLAN.md` as the human/agent handoff.
- The runner still removes per-phase artifacts before each invocation so interrupted runs cannot leak stale summaries or review findings into later phases.

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

This hardening is intentionally selective. Review and planning still rely on explicit artifacts where Ralph needs structured evidence, while development and fix stay focused on producing workspace side effects without extra submission ceremony.

## Claude/CCS MCP safety note

Claude-compatible transports such as `claude` and `ccs` run through a stricter MCP path. Ralph still uses `--mcp-config` plus `--strict-mcp-config`, but it only emits `--tools ""` / `--allowedTools ...` when live MCP tool discovery succeeds with a non-empty allowlist. That avoids a brittle edge case in non-interactive Claude/CCS runs where empty-tool configurations and MCP bootstrapping can produce misleadingly successful no-op executions.

## Parallel mode

When your planning phase produces two or more work units, Ralph fans development out across multiple git worktrees simultaneously. Configure it in your pipeline policy:

```toml
[pipeline.parallel_execution]
max_parallel_workers = 4
max_work_units = 50
```

See `docs/agents/parallelization.md` for the full guide.

## Transcript layout

Ralph emits every agent output line as a structured plain-text entry in the following format:

```
<ISO-TS> <LEVEL>  [<tag>][<unit>] <content>
```

**Tags** indicate the source and type of the line:

| Tag | Meaning |
|-----|---------|
| `phase` | Workflow phase transition (planning, development, review, …) |
| `plan` | Plan summary or scope |
| `plan-scope` | Plan scope items |
| `plan-steps` | Step progress |
| `activity` | Agent activity metadata (tool, path, workdir) |
| `activity-line` | Last raw activity line from an agent |
| `analysis` | Phase analysis and decision |
| `worker` | Parallel worker status update |
| `result` | Pipeline completion result |
| `pr` | Pull request URL |
| `artifact` | Artifact kind/summary |
| `content` | Agent text output (parsed from NDJSON) |
| `thinking` | Agent thinking/reasoning content (Claude extended thinking) |
| `tool` | Tool invocation (tool name) |
| `tool-result` | Tool result content |
| `error` | Error or malformed input |
| `progress` | Progress update |
| `status-content` | Status or lifecycle event from the agent |

**Levels** are `INFO`, `SUCCESS`, `WARN`, or `ERROR`.

**Oversized content** is condensed to a head+tail excerpt with a pointer:

```
2026-04-20T12:34:56Z INFO [content][dev-1] AAAAAAA … [+4200 chars, see .agent/raw/dev-1.log] … ZZZZZZZ
```

The full raw NDJSON output for each work unit is always written to `.agent/raw/<unit-id>.log` so you can inspect the complete output when needed.
