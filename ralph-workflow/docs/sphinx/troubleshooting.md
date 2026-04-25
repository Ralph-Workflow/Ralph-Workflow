# Troubleshooting

Frequently asked questions and common issues when using Ralph Workflow.

## PROMPT.md still has the starter sentinel

**Symptom:** Running `ralph` fails immediately with an error about the starter template.

**Cause:** The `PROMPT.md` file still contains the `<!-- ralph:starter-prompt ... -->` sentinel
that `ralph --init` places at the top. Ralph Workflow refuses to run while this sentinel is
present so you cannot accidentally run the pipeline against the placeholder task.

**Fix:** Open `PROMPT.md`, replace the example content with your actual task description,
and remove the sentinel comment at the top. Then re-run `ralph`.

See [Concepts](concepts.md) for what a good PROMPT.md should contain.

## No agents on PATH

**Symptom:** `ralph --diagnose` shows agents as `missing` in the PATH column, or the pipeline
fails when it tries to invoke an agent.

**Fix:** Install the agent binary and ensure it is on your `PATH`:

- **Claude Code**: see <https://docs.anthropic.com/claude-code>
- **opencode**: see <https://opencode.ai>

Verify after installation:

```bash
ralph --diagnose
```

The PATH column in the Agents table should show `on PATH` in green.

## MCP servers fail to start

**Symptom:** `ralph --check-mcp` or `ralph --diagnose` reports MCP server errors.

**Common causes and fixes:**

1. **Wrong command path** — check the `command` field in `.agent/mcp.toml`. Ensure the
   binary exists and is executable.
2. **Missing environment variables** — some MCP servers require API keys or tokens. Add
   them to your shell environment or to the `env` section in `.agent/mcp.toml`.
3. **Port conflict** — if your MCP server uses a fixed port, check that no other process
   is using it.

Validate after fixing:

```bash
ralph --check-mcp
```

## `make verify` fails after editing config

**Symptom:** `ruff`, `mypy`, or `pytest` fails after editing configuration or source files.

**Fix sequence:**

1. Run `make ruff-fix` to auto-fix lint issues.
2. Run `uv run python -m mypy ralph/` to find type errors and fix them manually.
3. Run `uv run pytest tests/ -q` to find failing tests and fix root causes.
4. Re-run `make verify` to confirm all checks pass.

Do not lower coverage thresholds or suppress warnings — fix the underlying issue.

## How to read a `[run-end]` block

The `[run-end]` block is emitted at the end of every pipeline run:

```
MILESTONE META [run-end] ◆ Ralph Workflow run end
INFO META [run-end] phase=complete
INFO META [run-end] elapsed=42.3s
INFO META [run-end] content_blocks=12
INFO META [run-end] thinking_blocks=4
INFO META [run-end] tool_calls=28
INFO META [run-end] errors=0
INFO META [run-end] agent_calls=7
```

Key fields:

| Field | Meaning |
|-------|---------|
| `phase` | Final phase reached (`complete` = success, `failed` = error) |
| `elapsed` | Total wall-clock time for the run |
| `content_blocks` | Number of agent text output blocks |
| `tool_calls` | Total MCP tool calls made by all agents |
| `errors` | Number of agent error events |
| `agent_calls` | Total agent subprocess invocations |

## When to use `--no-resume` vs `--resume`

| Flag | When to use |
|------|------------|
| `--resume` | You interrupted a run and want to continue from the last completed phase |
| `--no-resume` | The checkpoint is stale or from a different task; start fresh |
| (neither) | Default: Ralph Workflow uses a checkpoint if one exists, otherwise starts fresh |

Use `ralph --inspect-checkpoint` to see what the current checkpoint contains before deciding.

## Related pages

- [Quickstart](quickstart.md) — initial setup and first run
- [CLI Reference](cli.md) — all flags and sub-commands
- [Configuration Reference](configuration.md) — config file structure and FAQ
- [Recovery](recovery.md) — failure classification and retry behavior
