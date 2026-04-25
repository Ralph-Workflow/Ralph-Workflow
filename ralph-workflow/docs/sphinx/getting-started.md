# Getting Started with Ralph Workflow

New to Ralph Workflow? This walkthrough takes you from zero to your first pipeline run
without assuming any prior knowledge. If you are already familiar with the basics, see
[Quickstart](quickstart.md) for a concise reference.

## What is Ralph Workflow?

Ralph Workflow is an AI agent orchestration framework that automates implementation work.
You describe what you want built in a file called `PROMPT.md`, and Ralph Workflow
coordinates AI coding agents to plan, implement, review, and fix the work for you.

Under the hood, Ralph Workflow runs your AI agents through a structured sequence:
first a planning agent reads your goal and produces a structured plan, then a developer
agent writes the code, then a reviewer agent checks the result, and finally a fix agent
resolves any issues. The cycle repeats until the review passes or the configured limits
are reached.

You do not need to understand phrases like "phase", "drain", or "MCP artifact" to get
started — those are internal terms described in [Concepts](concepts.md) once you are
ready for them.

## Before You Start

You will need:

- **Python 3.12 or newer** — check with `python --version`
- **A git repository** — Ralph Workflow must run inside a git repo
- **At least one supported AI agent on PATH** — either `claude` (Claude Code) or
  `opencode` (OpenCode); see the install links below if you need to set one up

Install links:

- Claude Code: <https://docs.claude.com/claude-code>
- OpenCode: <https://opencode.ai>

## Install in 60 Seconds

```bash
pipx install ralph-workflow
```

Verify the install:

```bash
ralph --version
```

If `pipx` is not available, install it first:

```bash
python -m pip install pipx
python -m pipx ensurepath
```

## Your First Run

### 1. Navigate to a git repository

```bash
cd /path/to/your/project
```

Ralph Workflow must run inside a git repository. If you do not have one, create one:

```bash
git init my-project && cd my-project
```

### 2. Initialize Ralph Workflow

```bash
ralph --init
```

This creates `PROMPT.md` with a starter template and a `.agent/` directory with
configuration files. You will see a welcome panel listing what was created.

### 3. Edit PROMPT.md

Open `PROMPT.md` and replace the example content with your actual task:

```markdown
# Goal

Add a /health endpoint that returns HTTP 200 with {"status": "ok"}.

## Acceptance criteria

- GET /health returns HTTP 200
- Response body is valid JSON with status == ok
- A new test covers the endpoint
```

**Important:** Remove the `<!-- ralph:starter-prompt ... -->` comment at the very top
of the file. Ralph Workflow refuses to run while that sentinel is present — it is a
safety guard so you cannot accidentally run the placeholder task.

### 4. Verify your environment

```bash
ralph --diagnose
```

This prints a status table. Fix any ❌ rows before running the pipeline. Common issues:

- No agent on PATH → install `claude` or `opencode` (links above)
- Config errors → run `ralph --regenerate-config` to reset from defaults

### 5. Run the pipeline

```bash
ralph
```

Ralph Workflow starts running. Progress is shown inline. When it finishes, a summary
panel shows the result.

## What Happens During a Run

When you run `ralph`, four things happen in sequence:

- **Planning** — a planning agent reads your `PROMPT.md` and produces a structured plan
- **Development** — a developer agent implements the plan and writes code
- **Review** — a reviewer agent inspects the implementation for issues
- **Fix** — a fix agent resolves any issues the reviewer found

If the review finds significant problems, the review → fix cycle repeats up to the
configured limit (default: 2 times). See [Concepts](concepts.md) for the formal
definitions of each term.

## When Something Goes Wrong

**The sentinel comment is still in PROMPT.md**

```
PolicyValidationError: PROMPT.md is still the starter template
```

Open `PROMPT.md`, replace the example content with your task, and remove the
`<!-- ralph:starter-prompt ... -->` line at the top. See [Troubleshooting](troubleshooting.md).

**No agents found on PATH**

```
ralph --diagnose  # agents row shows ❌ or missing
```

Install `claude` or `opencode` and ensure the binary is on your `PATH`. See
[Troubleshooting](troubleshooting.md) for agent-specific install steps.

**Config errors in ralph --diagnose**

```bash
ralph --regenerate-config
```

This rewrites all config files from the bundled defaults. Existing files are backed up
with a `.bak` suffix. See [Configuration](configuration.md) for the full config reference.

## Next Steps

- [Quickstart](quickstart.md) — concise reference for repeat use
- [Concepts](concepts.md) — terminology: phases, drains, agents, MCP artifacts, checkpoints
- [CLI Reference](cli.md) — every flag and sub-command
- [Configuration](configuration.md) — config files, precedence, and FAQ
