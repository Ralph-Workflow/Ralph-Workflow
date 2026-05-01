# CLI Reference

> **New to Ralph Workflow?** Start with the [Getting Started](getting-started.md) walkthrough — it explains the same flow with more context.

Ralph Workflow is invoked as `ralph` (or `python -m ralph`). All flags are optional;
running `ralph` with no flags starts the full pipeline.

## Discovery and Diagnostics

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--list-agents` | | `False` | List all configured agents and their status |
| `--list-providers` | | `False` | List available AI providers (opencode API) |
| `--diagnose` | `-d` | `False` | Run full pre-flight diagnostics and print a status table |
| `--check-config` | | `False` | Load and validate configuration then exit |
| `--check-mcp` | | `False` | Validate custom MCP server definitions and exit |
| `--check-policy` | | `False` | Validate the active pipeline policy and print a summary, then exit |
| `--inspect-checkpoint` | | `False` | Print the contents of the current checkpoint |

### `--check-policy` example

```bash
ralph --check-policy
```

Validates the active pipeline policy (project-local `.agent/pipeline.toml` when present,
otherwise the bundled defaults) and prints a structured summary:

```
Policy OK: /path/to/.agent
  phases: 9
  drains: 9
  artifact contracts: 8
  loop counters: 2
  budget counters: 2
```

Exits 0 on success, 2 on `PolicyValidationError`, 1 on any other error.
Use `--explain-policy-dir` to point at a custom policy directory:

```bash
ralph --check-policy --explain-policy-dir /path/to/policy/dir
```

See [Policy Explanation](policy-explanation.md) for the full policy inspection command.

## Setup

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--init [label]` | | `None` | Scaffold `PROMPT.md` and `.agent/` config files; `ralph --init` (no label) is the recommended form — any label is deprecated and ignored |
| `--regenerate-config` | | `False` | Rewrite all configs from bundled defaults (existing files are backed up to `<name>.bak`) |

## Pipeline Tuning

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--counter NAME=VALUE` | | | Override a named budget or loop counter declared in `pipeline.toml` (e.g. `--counter iteration=3`). Repeatable. |
| `--developer-iters N` | `-D` | `5` | *(Deprecated — use `--counter` instead)* Maximum developer agent iterations per run. Overrides the `default_max` of the `iteration` loop/budget counter. |
| `--reviewer-reviews N` | `-R` | `2` | *(Deprecated — use `--counter` instead)* Maximum review-fix cycles (0 skips review entirely). Overrides the `default_max` of the `reviewer_pass` budget counter. |
| `--review-depth <mode>` | | `standard` | Review depth: `standard`, `comprehensive`, `security`, `incremental` |
| `--developer-agent <name>` | `-a` | (from config) | Override the developer agent by name |
| `--reviewer-agent <name>` | `-r` | (from config) | Override the reviewer agent by name |
| `--developer-model <flag>` | | (from config) | Model flag forwarded to the developer agent binary |
| `--reviewer-model <flag>` | | (from config) | Model flag forwarded to the reviewer agent binary |

## Execution Control

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--quiet` | `-q` | `False` | Suppress all output except errors |
| `--debug` | | `False` | Enable trace-level debug output |
| `--verbosity <level>` | `-v` | `verbose` | Set output verbosity: `quiet`, `normal`, `verbose`, `full`, `debug` |
| `--resume` | | `False` | Resume from the saved checkpoint if one exists |
| `--no-resume` | | `False` | Ignore the checkpoint and restart from the beginning |
| `--dry-run` | | `False` | Run the pipeline structure without invoking any agents |

> **Note:** Verbosity defaults to `verbose` (not `normal`) so Ralph Workflow is visibly
> active by default. Pass `--quiet` to silence everything except errors in CI.

## Commit Plumbing

These flags are used by Ralph Workflow's internal commit workflow and by the
`ralph --generate-commit` alias agents are instructed to call.

| Flag | Default | Description |
|------|---------|-------------|
| `--generate-commit-msg` | `False` | Generate a commit message from the current diff |
| `--generate-commit` | `False` | Generate a commit message and apply it in one step |
| `--show-commit-msg` | `False` | Print the most recently generated commit message |

## Git Identity

| Flag | Default | Description |
|------|---------|-------------|
| `--git-user-name <name>` | (from git config) | Override git `user.name` for commits made by Ralph Workflow |
| `--git-user-email <email>` | (from git config) | Override git `user.email` for commits made by Ralph Workflow |

## Miscellaneous

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--config <path>` | `-c` | (auto-discovered) | Path to a specific config file |
| `--version` | `-V` | `False` | Print the Ralph Workflow version and exit |

## Sub-commands

### `ralph cleanup`

Remove Ralph Workflow-generated working files from the current project (checkpoints,
MCP sockets, temp artifacts). Does not remove `.agent/` config files or `PROMPT.md`.

```bash
ralph cleanup
```

## Related pages

- [Getting Started](getting-started.md) — step-by-step first-run walkthrough
- [Configuration](configuration.md) — config files, flags, and precedence
- [Concepts](concepts.md) — pipeline phases, agents, and drains
- [Troubleshooting](troubleshooting.md) — common error messages and fixes
