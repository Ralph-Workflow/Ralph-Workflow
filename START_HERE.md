<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: de-duplicated the finish-receipt block. The verbatim
    empty-name-validation `development_result` block previously lived
    here AND on the root README AND on `ralph-workflow/START_HERE.md`
    AND on the Sphinx index. Root README is now the single source of
    the verbatim receipt; this page links there and uses a one-line
    summary instead.
  - Why it belongs here: this page is the public storefront's guided-
    first-run surface for a repo visitor. The receipt block is one
    click away via `README.md#what-a-run-leaves-you` so the reader
    still sees it, but the START_HERE page no longer competes with
    README as a second source of the verbatim artifact.
  - What was pruned, merged, or explicitly left alone: the duplicated
    finish-receipt code block (≈30 lines) is replaced by a 1-line
    pointer plus a 2-line summary. The rubric-aligned page-family
    minimum structure (evaluator kind → one realistic first-run goal
    → prerequisites → exact first steps → success signal → next click)
    is preserved as the spine.
  - How duplication was reduced or contained: root README is now the
    single source of the verbatim empty-name-validation receipt. The
    package-side START_HERE also defers to root README for the receipt.
    The diagnostics healthy-output sample is preserved here because
    `--diagnose` is a per-invocation check, not a receipt — it's the
    pre-flight that belongs on the first-run path, not the post-run
    artifact.
  - How the route is clearer now than before: evaluator role →
    prerequisites → exact first steps → pre-flight signal (verbatim)
    → post-run signal (pointer to README) → morning-after review note
    → next click.
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

<!--
  Canonical-source marker for the install block:
  README.md is the single source for this verbatim 6-step first-run block.
  The same block is intentionally duplicated in README.md#start-your-first-run
  so the first-run path on each page is self-contained. Future editors MUST
  keep both blocks in sync; this comment is the marker. The finish-receipt
  block defers to README.md#what-a-run-leaves-you as the single source.
-->

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
reconstructing the run. The canonical finish-receipt is in
[`README.md`](README.md) — root README is the single source for the
verbatim empty-name-validation receipt block ("What a run leaves you"
section). A successful run leaves a short artifact you can read in
under a minute: outcome, changed files, checks, reviewer focus.

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
- [docs/README.md](docs/README.md) — the docs map (routes by intent)
- [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) —
  the maintained operator manual
- [README.md](README.md) — back to the public storefront
