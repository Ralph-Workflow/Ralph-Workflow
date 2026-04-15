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
cd ralph-python
python -m pip install -e ".[dev]"
ralph --version
```

## Common commands

```bash
ralph --help
ralph --init feature-spec
ralph --diagnose
ralph --list-agents
ralph --list-providers
ralph --check-config
ralph --resume
ralph --inspect-checkpoint
ralph --generate-commit-msg
ralph --show-commit-msg
ralph --apply-commit
ralph --generate-commit
ralph-mcp
```

## Common flags

- `-D, --developer-iters`
- `-R, --reviewer-reviews`
- `-a, --developer-agent`
- `-r, --reviewer-agent`
- `--developer-model`
- `--reviewer-model`
- `-c, --config`
- `-d, --diagnose`
- `-i, --interactive`
- `-q, --quiet`
- `-v, --verbosity`
- `--review-depth`
- `--dry-run`
- `--no-resume`
- `--with-rebase`
- `--show-streaming-metrics`
- `-V, --version`

## Verification

```bash
cd ralph-python
make verify
```

## Package layout

- `ralph-python/ralph/cli/` — CLI entry points and commands
- `ralph-python/ralph/pipeline/` — state, events, reducer, orchestrator
- `ralph-python/ralph/phases/` — phase handlers
- `ralph-python/ralph/mcp/` — MCP bridge and standalone server
- `ralph-python/ralph/git/` — GitPython-backed operations
- `ralph-python/ralph/workspace/` — filesystem abstraction

## Legacy note

If you see older references to `cargo install`, crates, or Rust-only flags in archived docs, those describe the retired implementation, not the current Python CLI.