# Ralph Workflow Quick Reference

Current commands for the maintained Python package.

## Install

```bash
pip install ralph-workflow
# or
pipx install ralph-workflow
```

## Local development

```bash
cd ralph-workflow
python -m pip install -e ".[dev]"
ralph --version
```

## Common commands

```bash
ralph --help
ralph --init
ralph --diagnose
ralph --list-agents
ralph --list-providers
ralph --check-config
ralph --resume
ralph --inspect-checkpoint
ralph --generate-commit-msg
ralph --show-commit-msg
ralph --generate-commit
ralph-mcp
```

## Common flags

- `-Q, --quick` — quick mode: run a single developer iteration; accepts an inline prompt (`ralph -Q "do a quick change"`)
- `-D, --developer-iters` — maximum developer iterations (default: 5; `-Q` is equivalent to `-D 1`)
- `-a, --developer-agent`
- `--developer-model`
- `-c, --config`
- `-d, --diagnose`
- `-q, --quiet`
- `-v, --verbosity`
- `--dry-run`
- `--no-resume`
- `-V, --version`

## Verification

```bash
cd ralph-workflow
make verify
```

## Package layout

- `ralph-workflow/ralph/cli/` — CLI entry points and commands
- `ralph-workflow/ralph/pipeline/` — state, events, reducer, orchestrator
- `ralph-workflow/ralph/phases/` — phase handlers
- `ralph-workflow/ralph/mcp/` — MCP bridge and standalone server
- `ralph-workflow/ralph/git/` — GitPython-backed operations
- `ralph-workflow/ralph/workspace/` — filesystem abstraction

## Legacy note

If you see older references to `cargo install`, crates, or Rust-only flags in archived docs, those describe the retired implementation, not the current Python CLI.
