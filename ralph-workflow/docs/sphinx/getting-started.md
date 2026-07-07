# Getting Started with Ralph Workflow

This page walks you from install to one honest unattended run in a repository you already care about.
New to Ralph Workflow? This page takes you from install to one honest unattended run in a repository you already care about.
If you already know the shape of the product and just want the shortest checklist, use [Quickstart](quickstart.md).



## What this page gives you

Use this guide when you want the full first-run path, not just the short version:

- what to install before you try a run
- how to choose a task with a clear finish line
- what command to run first
- what good first-run output should look like
- where to go next if you need configuration or deeper operator docs

If you need config answers while reading, open [Configuration Reference](configuration.md).
If you want docs routed by use case instead of by document type, open [End-User Stories](agent-compatibility.md).

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
3. Run `ralph --diagnose` to verify your agents, MCP servers, and capability bundle are healthy (see [Diagnostics](diagnostics.md)).
4. Pick one real repo and one task with a clear finish line. **Bring your own authenticated agent CLI** — Ralph Workflow does not authenticate agents on your behalf (see [Agent CLI lifecycle](agents.md)).
5. Start with the default workflow instead of customizing immediately.
6. Let Ralph Workflow plan, implement, and verify the change.
7. Judge the result by the software change and the checks, not by transcript confidence alone.

That flow matters because Ralph Workflow is designed to give you a stronger unattended coding loop than a single long agent session.
The point of the first run is to see whether the default loop improves the repo in a way you can actually review.

After the first successful `ralph --init`, the bundled skill bundle is also auto-symlinked from `~/.claude/skills/` into the Codex (`~/.codex/skills/`), OpenCode (`~/.config/opencode/skills/`), and Google Anti Gravity (`~/.gemini/antigravity-cli/skills/`) skill roots so all four supported agents pick up the same baseline skills without extra setup. The bundled `.gitignore` template now also covers common Python, Node, editor, and OS artifacts (`__pycache__/`, `node_modules/`, `.idea/`, `.DS_Store`, and so on) so a fresh repo is batteries-included from the first commit. Pi.dev is wired as a transport but has no documented skill-discovery system per <https://pi.dev/docs/latest/usage>, so no Pi user-global install target is created and no `.pi/skills/` directory is written.

Install Pi.dev before referencing it in `ralph-workflow.toml`. Either:

```bash
# Option A — official install script (https://pi.dev/install.sh):
curl -fsSL https://pi.dev/install.sh | sh

# Option B — npm package (https://www.npmjs.com/package/@earendil-works/pi-coding-agent):
npm install -g @earendil-works/pi-coding-agent
```

After install, run `pi --version` to confirm the binary is on `PATH` before referencing the agent in `ralph-workflow.toml`.

**Note:** A normal `ralph` run (without `--init`) also auto-seeds the project-scope skills and the batteries-included `.gitignore` when missing, so the explicit first-run `ralph --init` step is no longer required for either artifact. Use `ralph --force-init-skills` to repair a conflict or overwrite the project-scope skills.

Outdated **user-global** skills are NOT auto-repaired on a normal `ralph` run; the run surfaces a `ralph --force-init-skills` hint instead. Run that flag to apply the update.

## Minimal first-run example

Install: see [README.md](../../README.md#start-your-first-run) for the canonical install + first-run walkthrough. The full `pipx install ralph-workflow`, `ralph --version`, `ralph --init`, and `ralph --diagnose` recipe with expected output lives in the root README only.

Move into the repository, scaffold it with `ralph --init`, and run `ralph --diagnose` — all covered in the canonical walkthrough at [README.md](../../README.md#start-your-first-run).


If an agent you want to use shows `missing`, install it before you run
`ralph`. If `Agent chains` is not `Satisfiable`, adjust your
`ralph-workflow.toml` or the agent selection in `PROMPT.md`.

Edit the run specification:

```bash
$EDITOR PROMPT.md
```

Example `PROMPT.md` starting point:

```md
# Goal
Ship one focused backlog task with tests or another real verification step.

## Constraints
- keep the change scoped to the task
- run the relevant checks before stopping
```

Run the unattended workflow:

```bash
ralph
```

Expected high-level result:

```text
─── [plan]
Planned: <N> steps to address the task
...
─── [verify]
make verify
...
─── [status]
Run completed. Review the branch/worktree before merging.
```

The exact transcript varies by task and agent. What matters is that the
run ends with a concrete change: modified files, test output, and a
completion artifact you can inspect. See
[Example Review Bundle](example-review-bundle.md) for what a complete
finish-receipt looks like.

## Validate the result in reality

Do not accept the run only because the transcript looks confident. The
uniquely human responsibility is to check the outcome against the real
world:

1. Run the program, tests, or checks yourself against real data or fixtures.
2. Exercise the changed feature with representative inputs.
3. Inspect the important files and artifacts the run produced.
4. Use code review as supporting evidence, not the only acceptance mechanism.
5. Decide the next action: push the branch, ask for changes, revert, rerun, or
discard the result.

You remain responsible for the destination and final judgment; the
finish-receipt tells you what to inspect. See
[Example Review Bundle](example-review-bundle.md) for what a complete
receipt contains.

If you need the underlying concepts first, open [Concepts](concepts.md).
If your first run goes sideways, use [Troubleshooting](troubleshooting.md).

## Recommended next clicks after your first run

- Need the shortest operator checklist? Use [Quickstart](quickstart.md).
- Need to inspect what trustworthy output looks like? Use [Example Review Bundle](example-review-bundle.md).
- Need to change settings or file locations? Use [Configuration Reference](configuration.md).
- Need docs by goal instead of by section? Use [End-User Stories](agent-compatibility.md).
