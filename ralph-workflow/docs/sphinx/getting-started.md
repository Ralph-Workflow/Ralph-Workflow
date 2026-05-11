# Getting Started with Ralph Workflow

New to Ralph Workflow? This walkthrough takes you from zero to your first pipeline run
without assuming any prior knowledge. If you are already familiar with the basics, see
[Quickstart](quickstart.md) for a concise reference.

## What is Ralph Workflow?

Ralph Workflow is a vendor-neutral AI coding workflow orchestrator for implementation work.
You describe what you want built in a file called `PROMPT.md`, and Ralph Workflow
routes AI coding agents to plan and implement the work for you.

It is designed for substantial work in **existing repositories** just as much as for brand-new
projects. A common use case is pointing it at an already-active codebase and asking it to
handle a meaningful feature, refactor, test expansion, or documentation pass.

Under the hood, Ralph Workflow runs your agents through a sequence of phases declared in
`.agent/pipeline.toml`. The runtime is a generic policy interpreter — all routing,
retry rules, analysis loops, and recovery behavior come from that file, not from
hardcoded logic. The bundled defaults provide a planning → development loop;
the workflow shape is fully configurable, and custom policies can add review/fix phases.

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

### 1. Navigate to your git repository

```bash
cd /path/to/your/project
```

Most teams use Ralph Workflow inside an **existing** git repository they already care about.
If you are trying it in a scratch repo instead, create one first:

```bash
git init my-project && cd my-project
```

### 2. Initialize Ralph Workflow

```bash
ralph --init
```

This creates `PROMPT.md` with a starter template plus the project-local `.agent/`
support files (`mcp.toml`, `pipeline.toml`, and `artifacts.toml`). You will see a
welcome panel listing what was created. If this repository needs its own main-config
override instead of inheriting from `~/.config/ralph-workflow.toml`, generate it explicitly:

```bash
ralph --generate-local-config
```

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

When you run `ralph`, the pipeline moves through the phases declared in
`.agent/pipeline.toml`. The bundled default workflow proceeds as follows:

1. **Planning** — a planning agent reads your `PROMPT.md` and produces a structured
   implementation plan
2. **Development** — a developer agent implements the plan and writes code (loops up
   to `-D`/`--developer-iters` times, default 5)
3. **Development analysis** — the pipeline evaluates the development output; loops
   back to development if more iteration is needed, otherwise proceeds
4. **Development commit** — the changes are committed to the repository
5. **Complete** — the pipeline ends successfully; if iterations remain (cap minus
   completed progress), the loop returns to planning for another cycle

This sequence is declared in `.agent/pipeline.toml`. Custom policies can add review
and fix phases on top of this base workflow. See [Configuration](configuration.md) to
customize phase routing, retry limits, and recovery behavior. See [Concepts](concepts.md)
for the formal definitions of each term.

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

## Related pages

- [Quickstart](quickstart.md) — concise reference for repeat use
- [Concepts](concepts.md) — pipeline phases, drains, agents, and MCP artifacts
- [CLI Reference](cli.md) — every flag and sub-command
- [Troubleshooting](troubleshooting.md) — common first-run issues and fixes
