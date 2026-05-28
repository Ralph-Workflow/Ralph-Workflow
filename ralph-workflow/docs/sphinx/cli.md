# CLI Reference

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) if you want the same flow with more context.

Ralph Workflow is invoked as `ralph` (or `python -m ralph`). Running `ralph` with no flags starts the normal workflow.

## Discovery and diagnostics

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--list-agents` | | `False` | List configured agents and their status |
| `--list-providers` | | `False` | List available AI providers (OpenCode API) |
| `--diagnose` | `-d` | `False` | Run pre-flight diagnostics and print a status table |
| `--check-config` | `-C` | `False` | Load and validate configuration, then exit |
| `--check-mcp` | | `False` | Validate custom MCP server definitions, then exit |
| `--check-policy` | | `False` | Validate the active pipeline policy and print a summary |
| `--inspect-checkpoint` | | `False` | Print the current checkpoint contents |

### `--check-policy` example

```bash
ralph --check-policy
```

This validates the active pipeline policy and prints a summary of the authored block model, compiled phases, drains, artifact contracts, and routing limits Ralph Workflow will use.

Use `--explain-policy-dir` to point at a custom policy directory:

```bash
ralph --check-policy --explain-policy-dir /path/to/policy/dir
```

See [Policy Explanation](policy-explanation.md) for the deeper inspection view.

## Setup

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--init [label]` | | `None` | Scaffold `PROMPT.md` plus project-local MCP, pipeline, and artifact files. Also installs Ralph Workflow's mirrored default skill bundle from the shipped package assets and runs a baseline capability health check. `ralph --init` with no label is the recommended form. |
| `--init-local-config` | | `False` | Create `.agent/` config files as explicit project-local copies of the main Ralph Workflow config set |
| `--regenerate-config` | | `False` | Rewrite config files from bundled defaults and keep backups as `<name>.bak` |

## Quick mode

Run one developer iteration with an inline prompt:

```bash
ralph -Q "do a quick change"
```

`-Q` / `--quick` forces `developer_iters=1` and lets you pass an inline prompt instead of using `PROMPT.md`. Ralph Workflow writes that inline prompt to `.agent/CURRENT_PROMPT.md` for the run.

```bash
ralph -Q "add a /healthz endpoint"
ralph -Q --prompt "add a /healthz endpoint"
```

## Prompt helper

The `--prompt-helper` flag launches a dedicated interactive prompt-refinement flow. Unlike the normal pipeline, this mode is simpler and does not use multi-stage workflows, drain configuration, or fallback agents. Instead, it runs a single PM-style agent that asks you what you want to build and helps you refine the idea into a structured `PROMPT.md`.

```bash
ralph --prompt-helper
```

The helper asks follow-up questions about users, goals, constraints, success criteria, product behavior, and UX/UI expectations. It periodically shows you a polished draft and asks for review. When you approve, it writes a structured `PROMPT.md` to the workspace root.

This is a simpler alternative to writing `PROMPT.md` by hand, not the standard pipeline. The resulting `PROMPT.md` can be used directly with the next `ralph` run.

The `ralph-prompt` executable is an alternate entrypoint for the same experience. Both `ralph --prompt-helper` and `ralph-prompt` launch identical interactive sessions:

```bash
ralph-prompt
```

`ralph-prompt` ships with Ralph Workflow and is installed automatically by `pip install ralph-workflow`. No separate install is needed.

## Thorough mode

Use the thorough preset when you want a longer unattended run budget:

```bash
ralph -T
```

`-T` / `--thorough` forces `developer_iters=10`. It cannot be combined with `-Q`.

## Pipeline tuning

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--counter NAME=VALUE` | | | Override a named budget or loop counter declared in `pipeline.toml` |
| `--developer-iters N` | `-D` | `5` | Maximum developer iterations per run |
| `--quick` | `-Q` | `False` | Quick mode: one developer iteration with optional inline prompt |
| `--thorough` | `-T` | `False` | Thorough mode: ten developer iterations |
| `--developer-agent <name>` | `-a` | (from config) | Override the developer agent by name |
| `--developer-model <flag>` | | (from config) | Forward a model flag to the developer agent binary |

## Execution control

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--quiet` | `-q` | `False` | Suppress all output except errors |
| `--debug` | | `False` | Enable trace-level debug output |
| `--verbosity <level>` | `-v` | `verbose` | Set output verbosity: `quiet`, `normal`, `verbose`, `full`, `debug` |
| `--resume` | `-r` | `False` | Resume from the saved checkpoint if one exists |
| `--no-resume` | | `False` | Ignore the checkpoint and restart from the beginning |
| `--dry-run` | | `False` | Run the pipeline structure without invoking agents |

> **Note:** Verbosity defaults to `verbose` so the run looks visibly alive by default. Use `--quiet` in CI when you only want errors.

## Commit-message helpers

These flags support Ralph Workflow's commit-message generation flow and the `ralph --generate-commit` command that agents may be instructed to call.

Commits created through this generated-commit path keep the active git author identity unless you override it, and Ralph Workflow appends a `Co-authored-by: Ralph Workflow <noreply@ralphworkflow.com>` trailer so automated commits stay attributable.

| Flag | Default | Description |
|------|---------|-------------|
| `--generate-commit-msg` | `False` | Generate a commit message from the current repo changes |
| `--generate-commit` | `False` | Generate and apply the commit message in one step |
| `--show-commit-msg` | `False` | Print the most recently generated commit message |

## Git identity

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

Remove Ralph Workflow-generated working files from the current project, such as checkpoints, MCP sockets, and temporary artifacts. It does not remove `.agent/` config files or `PROMPT.md`.

```bash
ralph cleanup
```

## Related pages

- [Getting Started](getting-started.md) — step-by-step first-run walkthrough
- [Configuration](configuration.md) — config files, flags, and precedence
- [Concepts](concepts.md) — the key workflow terms
- [Troubleshooting](troubleshooting.md) — common error messages and fixes
