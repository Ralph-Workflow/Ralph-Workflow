# Getting Started with Ralph Workflow

This page walks you from install to one honest unattended run in a
repository you already care about. It is the canonical first-run page
in the operator manual. For the shortest copy-paste path without task
templates, use the repo-root `START_HERE.md` first, then return here
when you need task-picking or `PROMPT.md` guidance.

## Goal

Ship one focused backlog task with tests or another real verification
step, end-to-end, and decide whether the result is something you would
actually merge.

## Prerequisites

Have these ready before you start:

- Python 3.12+
- One supported agent CLI already installed **and authenticated** (see [Agent CLI lifecycle](agents.md))
- One real git repo you care about
- One backlog task with a clear finish line (see [Pick the right first task](#pick-the-right-first-task))

## Exact steps

The install → init → diagnose → spec → run sequence is inlined below so
you can copy-paste without bouncing between files. Run every command
from a human-operated shell **outside any Ralph-managed agent session**.

### 1. Install Ralph Workflow

Install the bundled command and its baseline skills in one step:

```bash
pipx install ralph-workflow
```

Ralph Workflow needs Python 3.12 or newer. If you do not have `pipx`,
install it with `pip install --user pipx && pipx ensurepath` and restart
your shell.

### 2. Install and authenticate at least one agent CLI

Ralph Workflow orchestrates one of the agent CLIs you already trust. Pick
one and follow its native install + auth flow:

- **Claude Code** (`claude`): <https://docs.claude.com/claude-code>
- **Codex CLI** (`codex`): <https://github.com/openai/codex>
- **OpenCode** (`opencode`): <https://opencode.ai>
- **Nanocoder** (`nanocoder`), **Google Anti Gravity** (`agy`),
  **Pi** (`pi`), **Cursor** (`agent`): install and auth URLs for every agent
  are listed in [Agent Compatibility → Supported agents](agent-compatibility.md).

Verify the binary is on `PATH` before continuing:

```bash
claude --version     # or: codex --version / opencode --version / etc.
```

### 3. Initialize the project

From inside the git repo you want Ralph Workflow to operate on, run
`ralph --init`. The command:

1. Detects which agent CLIs are on `PATH` and enables them.
2. Creates the user-global config (`~/.config/ralph-workflow.toml`)
   and the policy defaults.
3. Writes a starter `PROMPT.md` (with a sentinel comment marking it as
   the starter template — Ralph Workflow refuses to run until you replace it).

```bash
ralph --init
```

`ralph --init` is safe to re-run; it is idempotent and re-checks
detected agents on every run. It does **not** create project-local TOMLs.
Those overrides are advanced and optional: create the complete
`.agent/ralph-workflow.toml`, `mcp.toml`, `pipeline.toml`, and
`artifacts.toml` set only with:

```bash
ralph --init-local-config
# Alias: ralph --generate-local-config
```

`ralph --regenerate-config` refreshes local TOMLs only when they already
exist (with backups); it never opts a project into local configuration.

### 4. Confirm the agent is wired up

Run `ralph --list-agents` (or `ralph --diagnose` for the full picture)
and confirm the agent you authenticated in step 2 shows up. If it does
not, the agent binary is not on `PATH` for the shell you launched
`ralph` from.

### 5. Run the pre-flight diagnostic

```bash
ralph --diagnose
```

Every line should be green before you spend a real run on it. If a
line is red, fix that line before continuing.

### 6. Edit `PROMPT.md`

Open the `PROMPT.md` file the init step created in your repo root and
replace the example content with your task. Remove the
`<!-- ralph:starter-prompt ... -->` sentinel comment at the top —
Ralph Workflow refuses to run with the starter template in place.

A small focused task fits four criteria (full guidance in
**Pick the right first task** below):

- **Clear boundary** — one sentence describes "done".
- **Clear correctness check** — tests, a script, or a recognisable diff.
- **Real but not critical** — a backlog item you would merge, not production.
- **2-6 hours of work**.

### 7. Start the run

```bash
ralph
```

The run walks the default phases: planning → development → commit
(see [Concepts](concepts.md) for the Ralph-loop mental model). Watch
the terminal for live progress; when the run finishes, Ralph Workflow
leaves a short summary of what changed, which checks ran, and what to
review. Durable run records also land under `.agent/` — see
[Artifacts reference](artifacts.md) if you need the full layout.

For deeper operator configuration, open
[Configuration Reference](configuration.md). For per-agent CLI, transport,
and model-string details, open the
[Agent Compatibility Guide](agent-compatibility.md).

## Pick the right first task

The most common mistake is starting with a task that is too ambitious
or too vague. Either kills trust in the tool, even when the tool would
have worked. A good first task fits four criteria:

1. **Clear boundary.** You should be able to describe what "done" means
   in one sentence. If you need a paragraph to define the boundary, the
   task is too big.
2. **Clear correctness check.** Something concrete must tell you
   whether the result is right — tests that pass, a script that runs
   end to end, a diff shape you can recognize at a glance.
3. **Real but not critical.** A backlog item you would actually merge,
   but not one where a mistake breaks production.
4. **2-6 hours of work.** Micro-tasks do not exercise the loop.
   Multi-day monsters do not let you build a tight feedback cycle.

- **Good candidates:** bounded refactor with tests, mechanical migration
  with existing tests, test-coverage pass for an untested module, focused
  docs/onboarding improvement, small feature slice with a visible endpoint.
- **Bad candidates:** vague "improve performance" tasks, exploratory
  "rewrite the entire frontend" assignments, anything that needs
  credentials the agent cannot reach.

For a worked reference of a small feature slice with a visible
endpoint, see the
[example-api proof page](https://github.com/Ralph-Workflow/Ralph-Workflow/blob/main/docs/examples/example-api.md)
— it is the canonical Ralph Workflow starter task (a Flask `/health`
endpoint) and the smallest end-to-end result the loop can produce.

## Write the spec in five minutes

`PROMPT.md` in your project root is the **run specification** the
agents consume. It is the single most important file you will write
between runs. The run spec is not a prompt: a prompt says
"please do this"; a spec defines what done means.

### The spec template

```markdown
## Task
[One sentence describing what to do.]

## Scope
- [Concrete boundary 1]
- [Concrete boundary 2]
- [Concrete boundary 3]

## What must not change
- [Behavior that must be preserved]
- [Tests that must still pass]
- [Public interfaces that must stay stable]

## Verification
- [ ] [Test or check 1]
- [ ] [Test or check 2]
- [ ] [Test or check 3]
```

The **"what must not change"** section is mandatory. Most prompts go
wrong by describing what to do but not what to protect. That section
makes the output reviewable — you scan the diff and check whether the
protected things stayed intact.

### Where the spec lives

`PROMPT.md` at the workspace root is the run specification you author.
Ralph Workflow reads that file (or another path you set with
`PROMPT_PATH`); keep edits in your authored spec, not in generated
runtime copies under `.agent/`. See
[Configuration Reference](configuration.md) for prompt-engine tuning.

### Picking the right depth preset

Depth presets trade wall-clock budget and thoroughness: `-Q` quick,
default standard, `-L` long, `-T` thorough. Details live in
[CLI Reference](cli.md). Most first-run tasks fit the default — skip
this until a short run feels under-scoped or over-long.

## First-task prompt templates

Pick the template closest to your real task, replace placeholders, keep
scope narrow, run, and ask one question in the morning: **does the
implementation hold up?**

### Template 1: Small feature slice

```markdown
# Goal
Add <feature> to <surface>. Keep the rest of the flow unchanged.

## Acceptance criteria
- <user action> now produces <expected result>
- Existing behavior for <adjacent flow> stays unchanged
- Tests cover the new behavior
- Documentation or help text is updated if user-visible behavior changed
```

### Template 2: Validation or guardrail

```markdown
# Goal
Reject or block <invalid input / unsafe action> before <bad outcome> happens.
Keep the normal success path unchanged.

## Acceptance criteria
- <invalid input> fails with a clear error or message
- <bad side effect> does not happen for invalid input
- Existing valid behavior stays unchanged
- Tests cover the new validation or guardrail
```

### Template 3: Bounded refactor with tests

```markdown
# Goal
Refactor <module / component / command> to improve <maintainability / duplication / structure>
without changing external behavior.

## Acceptance criteria
- Behavior stays the same for existing supported inputs
- The targeted duplication or structural problem is reduced
- Existing tests still pass
- New or updated tests cover the area if needed to lock behavior in place
```

### Template 4: Test coverage pass

```markdown
# Goal
Add or improve automated tests for <feature / module / workflow>.
Do not change production behavior unless a small testability fix is required.

## Acceptance criteria
- Tests cover the key success path for <feature>
- Tests cover at least one important failure or edge case
- Production changes stay minimal and scoped to testability if needed
- The relevant test command passes
```

### Template 5: Docs or onboarding improvement

```markdown
# Goal
Improve <doc / README / onboarding page> so a new user can complete <specific outcome>
without guessing.

## Acceptance criteria
- The doc clearly explains <specific concept or setup path>
- Steps are ordered and runnable
- Ambiguous wording or missing prerequisites are removed
- The updated doc matches current behavior in the codebase
```

## Project-local configuration overrides

The default `ralph --init` flow installs the bundled skill bundle and
seeds `.gitignore` coverage without creating `.agent/`. Project-local
configuration is advanced and optional. To create the complete local override
set explicitly, run `ralph --init-local-config` (alias:
`ralph --generate-local-config`), then edit `.agent/ralph-workflow.toml` in the
repo. `ralph --regenerate-config` refreshes local TOMLs only when they already
exist; it never opts a project into local configuration. The broader file
layout is in [Configuration Reference](configuration.md).

## Expected result / success check

Before you start, step 5's diagnostic should be fully green (see
[Diagnostics](diagnostics.md) if it is not).

After `ralph` returns, the run summary should name the change, the
checks, and the reviewer focus in a form you can read in under a
minute. For the on-disk artifact shapes behind that summary, see
[Artifacts reference](artifacts.md).

Then validate the result in reality — do not accept the run only
because the transcript looks confident: run the program, tests, or
checks yourself against real data or fixtures; exercise the changed
feature with representative inputs; inspect the important files the
run produced; use code review as supporting evidence, not the only
acceptance mechanism; decide the next action: push the branch, ask for
changes, revert, rerun, or discard the result.

## Next step

- Configuration answers → [Configuration Reference](configuration.md).
- Per-agent CLI/model-string reference → [Agent Compatibility Guide](agent-compatibility.md).
- Underlying concepts → [Concepts](concepts.md).
- First run goes sideways → [Troubleshooting](troubleshooting.md).
