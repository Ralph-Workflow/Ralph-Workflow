# Getting Started with Ralph Workflow

New to Ralph Workflow? This page takes you from install to one honest unattended run in a repository you already care about.
If you already know the shape of the product and just want the shortest checklist, use [Quickstart](quickstart.md).

Ralph Workflow is a free and open-source AI agent orchestration system built around a simple Ralph loop core.
It turns that simple structure into a stronger composable workflow for substantial, well-specified repo work by moving through planning, implementation, and verification instead of stopping at one long agent session.
The default workflow is already strong for writing software; start there first, then extend later only when you know why.

## What this page gives you

Use this guide when you want the full first-run path, not just the short version:

- what to install before you try a run
- how to choose a task with a clear finish line
- what command to run first
- what good first-run output should look like
- where to go next if you need configuration or deeper operator docs

If you need config answers while reading, open [Configuration Reference](configuration.md).
If you want docs routed by use case instead of by document type, open [End-User Stories](user-stories.md).

## Before you run Ralph Workflow

Start with a real repository and a task you could judge without reading an entire transcript.
Good first tasks usually have these properties:

- the expected change is visible in the repo or product behavior
- meaningful checks already exist, or you can add one small check
- the task is substantial enough to benefit from planning and verification
- the finish line is concrete enough that you can say whether the run succeeded

If you are unsure what counts as a good task, use [First Task Guide](first-task-guide.md) before you run anything.

## First-run flow

1. Install Ralph Workflow and confirm the CLI is available.
2. Initialize the repo with `ralph --init`.
3. Pick one real repo and one task with a clear finish line.
4. Start with the default workflow instead of customizing immediately.
5. Let Ralph Workflow plan, implement, and verify the change.
6. Judge the result by the software change and the checks, not by transcript confidence alone.

That flow matters because Ralph Workflow is designed to give you a stronger unattended coding loop than a single long agent session.
The point of the first run is to see whether the default loop improves the repo in a way you can actually review.

After the first successful `ralph --init`, the bundled skill bundle is also auto-symlinked from `~/.claude/skills/` into the Codex (`~/.codex/skills/`), OpenCode (`~/.config/opencode/skills/`), and Google Anti Gravity (`~/.gemini/antigravity-cli/skills/`) skill roots so all four supported agents pick up the same baseline skills without extra setup. The bundled `.gitignore` template now also covers common Python, Node, editor, and OS artifacts (`__pycache__/`, `node_modules/`, `.idea/`, `.DS_Store`, and so on) so a fresh repo is batteries-included from the first commit.

**Note:** A normal `ralph` run (without `--init`) also auto-seeds the project-scope skills and the batteries-included `.gitignore` when missing, so the explicit first-run `ralph --init` step is no longer required for either artifact. Use `ralph --force-init-skills` to repair a conflict or overwrite the project-scope skills.

Outdated **user-global** skills are NOT auto-repaired on a normal `ralph` run; the run surfaces a `ralph --force-init-skills` hint instead. Run that flag to apply the update.

## Minimal first-run example

```bash
pipx install ralph-workflow
cd /path/to/your/repo
ralph --init
$EDITOR PROMPT.md
ralph
```

Example `PROMPT.md` starting point:

```md
# Goal
Ship one focused backlog task with tests or another real verification step.

## Constraints
- keep the change scoped to the task
- run the relevant checks before stopping
```

If you need the underlying concepts first, open [Concepts](concepts.md).
If your first run goes sideways, use [Troubleshooting](troubleshooting.md).

## Recommended next clicks after your first run

- Need the shortest operator checklist? Use [Quickstart](quickstart.md).
- Need to inspect what trustworthy output looks like? Use [What Good Ralph Workflow Output Looks Like](reviewable-output.md).
- Need to change settings or file locations? Use [Configuration Reference](configuration.md).
- Need docs by goal instead of by section? Use [End-User Stories](user-stories.md).
