# Ralph Workflow Quick Reference

Current commands for the maintained Python package.

## Table of Contents

- [Install](#install)
- [Common Commands](#common-commands)
- [Common Flags](#common-flags)
- [Verification](#verification)
- [Package Layout](#package-layout)

## Install

```bash
pip install ralph-workflow
# or
pipx install ralph-workflow
```

## Local Development

```bash
cd ralph-workflow
python -m pip install -e ".[dev]"
ralph --version
```

## Common Commands

| Command | Description |
|---------|-------------|
| `ralph --help` | Show help |
| `ralph --init` | Initialize workspace |
| `ralph --diagnose` | Run diagnostics |
| `ralph --list-agents` | List configured agents |
| `ralph --list-providers` | List available providers |
| `ralph --check-config` | Validate configuration |
| `ralph --resume` | Resume interrupted session |
| `ralph --inspect-checkpoint` | Inspect checkpoint state |
| `ralph --generate-commit-msg` | Generate commit message |
| `ralph --show-commit-msg` | Show current commit message |
| `ralph --generate-commit` | Create commit |
| `ralph-mcp` | Start MCP server |

## Common Flags

### Iteration Control

- `-Q, --quick` — quick mode: run a single developer iteration with inline prompt (`ralph -Q "do a quick change"`)
- `-D, --developer-iters` — maximum developer iterations (default: 5; `-Q` is equivalent to `-D 1`)

### Agent Selection

- `-a, --developer-agent` — set developer agent
- `--developer-model` — set developer model

### General Options

- `-c, --config` — path to config file
- `-d, --diagnose` — run diagnostics mode
- `-q, --quiet` — suppress output
- `-v, --verbosity` — set verbosity level
- `--dry-run` — dry run mode
- `--no-resume` — disable resume
- `-V, --version` — show version

## Verification

```bash
cd ralph-workflow
make verify
```

## Package Layout

```
ralph-workflow/ralph/
├── cli/              # CLI entry points and commands
├── pipeline/         # State, events, reducer, orchestrator
├── phases/           # Phase handlers
├── mcp/              # MCP bridge and standalone server
├── git/              # GitPython-backed operations
└── workspace/        # Filesystem abstraction
```

## Legacy Note

If you see older references to `cargo install`, crates, or Rust-only flags in archived docs, those describe the retired implementation, not the current Python CLI.
