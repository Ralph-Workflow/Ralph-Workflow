# Getting Started with Ralph Workflow

This page walks you from install to one honest unattended run in a
repository you already care about. It is the canonical first-run page
in the operator manual and the single source of truth for everything
between `pipx install ralph-workflow` and the morning-after review.

## Goal

Ship one focused backlog task with tests or another real verification
step, end-to-end, and decide whether the result is something you would
actually merge.

## Prerequisites

Have these ready before you start:

- Python 3.12+
- One supported agent CLI already installed **and authenticated**
  (see [Agent CLI lifecycle](agents.md) for the selection and
  trust-boundary story)
- One real git repo you care about
- One backlog task with a clear finish line (see
  [Pick the right first task](#pick-the-right-first-task))

## Exact steps

The canonical six-step install → init → diagnose → spec → run → review
sequence is in the repository root
[`README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md#start-your-first-run).
Run those commands from a human-operated shell outside any Ralph-managed
agent session. The Sphinx manual defers to that source for the canonical
walkthrough so the install commands stay in one place.

Briefly:

1. **Install** with `pipx install ralph-workflow`.
2. **Initialize** the repo with `ralph --init`.
3. **Pre-flight** with `ralph --diagnose` to verify agents, MCP servers,
   and capability bundles are healthy (see [Diagnostics](diagnostics.md)).
4. **Write** the run spec at `PROMPT.md` — see
   [First-task prompt templates](#first-task-prompt-templates) below.
5. **Run** with `ralph`.
6. **Review** the morning-after handoff — see
   [Proof: what a run leaves you](#proof-what-a-run-leaves-you).

If a deeper operator configuration is required, open
[Configuration Reference](configuration.md). For docs grouped by use
case instead of by document type, open
[End-User Stories](agent-compatibility.md).

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

Good candidates: bounded refactor with tests, mechanical migration with
existing tests, test-coverage pass for an untested module, focused
docs/onboarding improvement, small feature slice with a visible
endpoint. Bad candidates: vague "improve performance" tasks,
exploratory "rewrite the entire frontend" assignments, anything that
requires access to SaaS dashboards the agent cannot reach.

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

### Where the spec lives vs. what the engine renders

`PROMPT.md` at the workspace root is the run specification you author.
The engine materializes its own consumption copy at
`.agent/CURRENT_PROMPT.md`; you never edit the materialised file.
Operators who want a non-default location override the path through
`PROMPT_PATH`. The agent-side Jinja2 template machinery (which the
older `prompts.md` page documented) lives in
`ralph/prompts/`; you do not author those templates — they are the
maintainer-contributed details that translate your run spec into
agent input. See [Configuration Reference](configuration.md) for
`RALPH_INLINE_SKILLS_DIR` and prompt-engine tuning.

### Picking the right depth preset

```bash
ralph -Q     # quick: small fixes, single iteration
ralph        # standard: most features and tasks
ralph -T     # thorough: complex refactors, ten iterations
```

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
seeds `.gitignore` coverage. For explicit project-local overrides of
the main `ralph-workflow.toml`, run `ralph --init-local-config` and
edit `.agent/ralph-workflow.toml` in the repo. That local file
belongs to the opt-in override flow, not the default `ralph --init`
path. The broader file layout is in
[Configuration Reference](configuration.md).

## Policy-driven migration note (historical)

If you are upgrading from an earlier Ralph Workflow release, the
pipeline is fully policy-driven: routing, retry rules, analysis
loops, commit semantics, verification gates, recovery routing, and
terminal behavior all come from `pipeline.toml`. Older assumptions
about implicit phase names, implicit loop counters, and pseudo-phase
recovery aliases (`"phase_failed"`, `"failed"`) are no longer valid.
To migrate an existing `.agent/pipeline.toml`, run
`ralph --regenerate-config` and diff against your existing file. Full
migration details — including the rejected legacy fields, removed
hidden behaviors, and verification commands — are folded into
[Configuration Reference](configuration.md) under "Policy migration
reference".

## Proof: what a run leaves you

This is the actual finish-receipt from a real bundled example — a real,
unedited handoff you read in the morning instead of a transcript:

```text
# Development Result

## Outcome
Implemented empty-name validation in the CLI create flow and added
test coverage for empty and whitespace-only input.

## Changed files
- cli/create.py
- tests/test_create.py

## Checks run
- pytest tests/test_create.py        ✓ passed
- project formatting / lint checks    ✓ passed

## Reviewer focus
- confirm validation happens before any file creation side effect
- confirm the error message is clear enough for CLI users
- confirm no unrelated flow changed
```

The live, inspectable example bundle sits at
[`examples/first-review-bundle/`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/examples/first-review-bundle).
Open the `PROMPT.md`, the `DEVELOPMENT_RESULT.md`, the
`ISSUES.md`, the `FIX_RESULT.md`, and the JSON artifacts to see the
machine-readable trail. The receipt tells you what changed, what
checks ran, and what to inspect — without reconstructing the run.

## Expected result / success check

A successful first run produces two concrete signals:

1. `ralph --diagnose` should report every line green before you
   start. If any line is red, fix that line before you spend a real
   run on it. See [Diagnostics](diagnostics.md) for the failure-mode
   table.
2. After `ralph` returns, open the finish-receipt artifact. It
   names the change, the checks, and the reviewer focus in a form you
   can read in under a minute.

Then validate the result in reality — do not accept the run only
because the transcript looks confident:

1. Run the program, tests, or checks yourself against real data or
   fixtures.
2. Exercise the changed feature with representative inputs.
3. Inspect the important files and artifacts the run produced.
4. Use code review as supporting evidence, not the only acceptance
   mechanism.
5. Decide the next action: push the branch, ask for changes, revert,
   rerun, or discard the result.

## Next step

- Need configuration answers? Open
  [Configuration Reference](configuration.md).
- Need docs grouped by use case? Open
  [End-User Stories](agent-compatibility.md).
- Need the morning-after merge check shape? The "five-minute merge
  check" pattern is folded into this page under
  [Proof: what a run leaves you](#proof-what-a-run-leaves-you).
- If your first run goes sideways, use [Troubleshooting](troubleshooting.md).
- If you need the underlying concepts first, open [Concepts](concepts.md).