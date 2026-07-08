# Start Here

> **Primary:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub mirror: <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

Pick one real task you already want done, run it unattended, then judge
the result by what the software actually does and what checks ran.

## Prerequisite

One supported agent CLI installed and authenticated. See
[Agent CLI lifecycle](ralph-workflow/docs/sphinx/agents.md) for the
selection and trust-boundary story.

## Install → init → diagnose → spec → run → review

The canonical six-step sequence is in
[`README.md`](README.md#start-your-first-run). `ralph --diagnose` is
the pre-flight check — it shows which baseline helpers are healthy,
missing, or need repair before you spend a real run on them.

## Success looks like

1. `ralph --diagnose` is all-healthy before you start.
2. After `ralph` returns, the finish-receipt artifact names the change,
   the checks, and the review focus. See
   [Getting Started → Proof: what a run leaves you](ralph-workflow/docs/sphinx/getting-started.md#proof-what-a-run-leaves-you).

Open the diff, run the program against your real environment, exercise
the feature, then decide the next action.

## Where to go next

- [README.md](README.md) — public storefront
- [docs/README.md](docs/README.md) — docs map
- [ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst) —
  the operator manual
