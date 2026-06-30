<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: clarified the role of the repo-root START_HERE.md as the
    shortest serious first-run path for a repo visitor, separate from the
    package-side START_HERE.md (for someone who installed via pipx and is
    reading the package directory). Both now share the canonical autopilot
    positioning and the install/ralph --init/first-run sequence from the
    top-level README.
  - Why it belongs here: this page is the public storefront's guided-first-run
    surface. A repo visitor landing on this page should know what they will
    evaluate for, what success looks like, and where to go next — without
    restating the README's full positioning.
  - What was pruned, merged, or explicitly left alone: redundant framing
    prose is trimmed; the rubric-aligned page-family minimum structure
    (evaluator kind → one realistic first-run goal → prerequisites → exact
    first steps → success signal → next click) is preserved as the spine.
    Codeberg pointer, README + docs map + operator manual next-click chain
    stays.
  - How duplication was reduced or contained: the package-side START_HERE
    gets a parallel role-specific page; the top-level README keeps install
    + first-run. No two surfaces now fight or duplicate each other. Both
    surfaces share the same ordered first-run sequence — `pipx install`
    → `cd <repo>` → `ralph --init` → `ralph --diagnose` →
    `$EDITOR PROMPT.md` → `ralph` — matching the top-level README so a
    reader landing on either START_HERE sees the same install + first-run
    path.
  - How the route is clearer now than before: a clear evaluator role
    announcement up front, then prerequisites, then exact first steps with
    verification signals after each command.
-->

# Start Here: try Ralph Workflow on one real backlog task

> **Codeberg is the primary repo.** Star, watch, and report issues there:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror.

Ralph Workflow is **the autopilot for coding agents** — a free and
open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes powerful
through composition.

**Hand it a well-specified coding task, let the agents plan, build,
verify, and fix, and come back to reviewable, tested work.**

This is the shortest serious first-run path for a repo visitor.
The default workflow is strong enough to adopt as-is, before you
customize anything.

## What kind of evaluator is this for

You are considering Ralph Workflow for **one real backlog task** — the
kind of ambitious, well-specified work that is too big to babysit and
too risky to trust blindly. If a task needs more than one prompt, more
than one verification step, or more trust than you want to place in a
single agent session, this path is for you.

## One realistic first-run goal

Pick **one real backlog task** you already want done. Run it unattended.
Judge the result by what the software actually does and what checks ran —
the morning-after review matters more than the running transcript.

## Prerequisites

Have these ready before you start:

- One real git repo you care about
- Python 3.12+
- One supported agent CLI already installed **and authenticated** (see
  [Agent CLI lifecycle](ralph-workflow/docs/sphinx/agents.md) for the
  selection and trust-boundary story)
- Working authentication for that agent

## Exact first steps

Run these commands from a human-operated shell outside any Ralph-managed
agent session:

```bash
pipx install ralph-workflow        # 1. install the autopilot
cd /path/to/your/project           # 2. pick a real repo
ralph --init                       # 3. scaffold .agent/ + PROMPT.md
ralph --diagnose                   # 4. pre-flight: verify agents, MCP, capabilities
$EDITOR PROMPT.md                  # 5. write the task — see PROMPT.md template
ralph                              # 6. run the unattended workflow
```

- `ralph --init` provisions the default local work surface and shipped
  baseline skills.
- `ralph --diagnose` is the **pre-flight check** — it shows which
  baseline helpers are healthy, missing, unreachable, degraded, or
  need repair before you spend a real run on them. See
  [diagnostics.md](ralph-workflow/docs/sphinx/diagnostics.md) for what
  each check proves.

## What success looks like

A successful first run produces two concrete signals you can read the
morning after.

### `ralph --diagnose` should report all-healthy before you start

After step 4 in [Exact first steps](#exact-first-steps), the pre-flight
report should show every line green, with no missing, degraded, or
needs-repair signals:

```text
Ralph Workflow Diagnostics
—————————————————————————————
✓ Git repository
✓ Configuration
✓ Agents (claude, opencode)
✓ MCP servers (3 upstreams reachable)
✓ Workspace files
✓ Capability state (12/12 healthy)
✓ Pre-flight policy validation

All checks passed. Ready for `ralph`.
```

If a line is red, `--diagnose` tells you what is missing (e.g. an agent
CLI not on `PATH`), unreachable (e.g. an MCP upstream), or degraded
(e.g. a capability whose provider key is unset). Fix that line before
you spend a real run on it. The full failure-mode table is in
[diagnostics.md](ralph-workflow/docs/sphinx/diagnostics.md).

### A successful run leaves a finish-receipt you can review

After step 6 returns, you should find a `development_result` artifact
that names the change, the checks, and the reviewer focus without
reconstructing the run. Here is a compact example reusing the same
empty-name-validation task already referenced in the README and the
Sphinx manual:

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

After a good first run you should be able to point to:

- A real repo change that matches the written task
- Meaningful checks that ran and reported clear outcomes
- A `development_result` artifact you can review without reconstructing
  the whole run
- A clear sense of whether the default workflow helped enough to keep
  using

The morning-after review matters more than the running transcript.
Open the diff, run the program against your real environment, exercise
the feature, check the receipts, then decide the next action.

## Where to go next

- [first-task-guide.md](ralph-workflow/docs/sphinx/first-task-guide.md) —
  choose the right task before you draft your spec
- [diagnostics.md](ralph-workflow/docs/sphinx/diagnostics.md) — what
  `ralph --diagnose` actually checks
- [agents.md](ralph-workflow/docs/sphinx/agents.md) — selection,
  authentication, and invocation for every supported agent
- [after-your-first-run.md](ralph-workflow/docs/sphinx/after-your-first-run.md) —
  turn a real run into a Codeberg signal
- [docs/README.md](docs/README.md) — the docs map (routes by intent)
- [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) —
  the maintained operator manual
- [README.md](README.md) — back to the public storefront
