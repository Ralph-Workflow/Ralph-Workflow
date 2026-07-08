# Start Here

> **Codeberg is primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror:
> <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

The shortest serious first-run path for a repo visitor. Pick one real
backlog task you already want done. Run it unattended. Judge the result
by what the software actually does and what checks ran.

## Prerequisite

One supported agent CLI already installed **and authenticated**. See
[Agent CLI lifecycle](ralph-workflow/docs/sphinx/agents.md) for the
selection and trust-boundary story.

## Install, init, diagnose, spec, run, review

The canonical six-step sequence is in
[`README.md`](README.md#start-your-first-run). Run those commands from
a human-operated shell outside any Ralph-managed agent session.

`ralph --diagnose` is the **pre-flight check** — it shows which
baseline helpers are healthy, missing, unreachable, degraded, or need
repair before you spend a real run on them. If a line is red, `--diagnose`
tells you what to fix.

## Success looks like

A successful first run produces two concrete signals:

1. `ralph --diagnose` reports all-healthy before you start. See
   [diagnostics.md](ralph-workflow/docs/sphinx/diagnostics.md) for the
   full failure-mode table.
2. After `ralph` returns, the finish-receipt artifact names the change,
   the checks, and the reviewer focus. The canonical finish-receipt
   shape and the "validate in reality" review checklist are in
   [Getting Started → Proof: what a run leaves you](ralph-workflow/docs/sphinx/getting-started.md#proof-what-a-run-leaves-you).

Open the diff, run the program against your real environment, exercise
the feature, check the receipts, then decide the next action.

## Where to go next

- [README.md](README.md) — public storefront
- [docs/README.md](docs/README.md) — docs map (routes by intent)
- [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) —
  the maintained operator manual
