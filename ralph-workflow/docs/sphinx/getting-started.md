# Getting Started with Ralph Workflow

New to Ralph Workflow? This page takes you from install to your first unattended run without assuming you already know the pipeline internals. If you want the same flow in shorter form, use [Quickstart](quickstart.md).

## What Ralph Workflow does

Ralph Workflow is a repo-native orchestration CLI for bigger AI coding tasks. You describe the task in `PROMPT.md`, Ralph Workflow runs planning, coding, and agent review, and you come back to completed work, logs, and artifacts you can inspect in your normal git workflow.

It works well for substantial work in **existing repositories** as well as new ones: feature work, refactors, test expansion, documentation passes, and similar multi-file tasks.

## Before you start

You will need:

- **Python 3.12 or newer** — check with `python --version`
- **A git repository** — Ralph Workflow runs inside a git repo
- **At least one supported AI agent on your PATH** — usually `claude` (Claude Code) or `opencode` (OpenCode)

Install links:

- Claude Code: <https://docs.claude.com/claude-code>
- OpenCode: <https://opencode.ai>

## Install in 60 seconds

```bash
pipx install ralph-workflow
ralph --version
```

If `pipx` is not available yet:

```bash
python -m pip install pipx
python -m pipx ensurepath
```

## Your first run

### 1. Go to your git repository

```bash
cd /path/to/your/project
```

Most teams use Ralph Workflow in an existing repository they already care about. If you are trying it in a scratch repo instead, create one first:

```bash
git init my-project && cd my-project
```

### 2. Initialize Ralph Workflow

```bash
ralph --init
```

This creates `PROMPT.md` plus the project-local `.agent/` support files Ralph Workflow needs to run.

If this repository also needs a project-local copy of the main Ralph Workflow config, create it explicitly:

```bash
ralph --init-local-config
```

### 3. Edit `PROMPT.md`

Open `PROMPT.md` and replace the example content with your actual task:

```markdown
# Goal

Add a /health endpoint that returns HTTP 200 with {"status": "ok"}.

## Acceptance criteria

- GET /health returns HTTP 200
- Response body is valid JSON with status == ok
- A new test covers the endpoint
```

**Important:** remove the `<!-- ralph:starter-prompt ... -->` comment at the top. Ralph Workflow refuses to run while that sentinel is still present so you do not accidentally launch the placeholder task.

### 4. Verify the environment

```bash
ralph --diagnose
```

This is the recommended pre-flight check before the first real run. Fix any ❌ rows before continuing. Common issues:

- No agent on PATH → install `claude` or `opencode`
- Config errors → run `ralph --regenerate-config`

### 5. Start the run

```bash
ralph
```

Ralph Workflow shows progress inline while it runs. When it finishes, you come back to completed work, logs, and artifacts you can inspect before deciding what to do next.

## What happens during a run

You do not need the full internal model to operate Ralph Workflow. The short version is:

1. **Planning** — Ralph Workflow turns your task into a plan
2. **Development** — an implementation agent works through the plan
3. **Analysis and review** — Ralph Workflow checks the result, decides whether more work is needed, and records review output
4. **Completion** — the run ends with the resulting changes, logs, and artifacts saved in the repo

If you later want the deeper mechanics — phases, drains, loopbacks, policy files, and artifact contracts — see [Concepts](concepts.md) and [Configuration](configuration.md).

## When something goes wrong

**The sentinel comment is still in `PROMPT.md`**

```
PolicyValidationError: PROMPT.md is still the starter template
```

Replace the example task and remove the `<!-- ralph:starter-prompt ... -->` line.

**No agents found on PATH**

```
ralph --diagnose
```

Install `claude` or `opencode`, then run the diagnostic again.

**Config errors in `ralph --diagnose`**

```bash
ralph --regenerate-config
```

This rewrites config files from the bundled defaults and keeps backups with a `.bak` suffix.

## Next steps

- [Quickstart](quickstart.md) — shorter repeat-use reference
- [Concepts](concepts.md) — the terms you will see most often
- [CLI Reference](cli.md) — commands and flags
- [Configuration](configuration.md) — config files and precedence
- [Troubleshooting](troubleshooting.md) — common first-run problems
