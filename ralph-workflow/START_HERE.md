<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: de-duplicated the finish-receipt block and the
    `ralph --diagnose` healthy-output sample. The verbatim
    empty-name-validation `development_result` block previously lived
    here AND on the root README AND on `START_HERE.md` AND on the
    Sphinx index; root README is now the single source for the
    verbatim receipt and this page defers to it. The pre-flight
    `--diagnose` sample is also preserved in the repo-root
    `START_HERE.md`, so this page now points there instead of
    restating the same block.
  - Why it belongs here: the page lives inside `ralph-workflow/`, which
    is the package directory a pipx-installed reader explores after
    `ralph --help`. The role boundary (package operator vs. repo
    visitor) keeps the route coherent instead of duplicating the
    repo-root START_HERE.
  - What was pruned, merged, or explicitly left alone: the duplicated
    finish-receipt code block (≈30 lines) and the duplicated
    `ralph --diagnose` healthy-output sample (≈12 lines) are replaced
    by pointers to the canonical surfaces. The package-operator-only
    framing (no global pipx install step, since the package is
    already installed by definition) is preserved. The rubric-aligned
    page-family minimum structure (evaluator kind → one realistic
    first-run goal → prerequisites → exact first steps → success
    signal → next click) is preserved as the spine.
  - How duplication was reduced or contained: this page is the
    package-side twin of the repo-root START_HERE — same sequence, but
    framed for a reader who has already installed and wants the
    operator fast-path. Receipt and pre-flight output live once each
    on the root surfaces.
  - How the route is clearer now than before: explicit role boundary
    up front, then prerequisites, then exact steps, then a pointer
    to the canonical pre-flight and receipt, then a short next-click
    chain into the operator manual.
-->

# Start Here: first run as a package operator

> **Codeberg is the primary repo.** Star, watch, and report issues there:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror.

Ralph Workflow is **the autopilot for coding agents** — a free and
open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes powerful
through composition.

**Hand it a well-specified coding task, let the agents plan, build,
verify, and fix, and come back to reviewable, tested work.**

This page is the **package-operator first-run path** for a reader who
has just run `pipx install ralph-workflow` and is exploring the
package directory. The default workflow is strong enough to adopt
as-is, before you customize anything.

## What kind of evaluator is this for

You have the package installed and want to run it on **one real backlog
task** you already want done. You are past evaluation and want the
shortest path to a real run that ships reviewable, tested work.

## One realistic first-run goal

Pick **one small, concrete task** with clear acceptance criteria — the
kind of work you would actually merge. A good first run is one sitting,
has a clear boundary, and a concrete correctness check.

## Prerequisites

Have these ready before you start:

- One real git repo you care about
- Python 3.12+ (the package itself, no other setup needed)
- One supported agent CLI already installed **and authenticated** (see
  [Agent CLI lifecycle](docs/sphinx/agents.md) for the selection and
  trust-boundary story)
- Working authentication for that agent

## Exact first steps

Run these commands from a human-operated shell outside any Ralph-managed
agent session:

```bash
cd /path/to/your/project      # move into the repo you want agents on
ralph --init                  # scaffold .agent/ + PROMPT.md
ralph --diagnose              # pre-flight: agents, MCP, capabilities
$EDITOR PROMPT.md             # write your spec for the run
ralph                         # run the unattended workflow
```

- `ralph --init` provisions the default local work surface and shipped
  baseline skills.
- `ralph --diagnose` is the **pre-flight check** — it shows which
  baseline helpers are healthy, missing, unreachable, degraded, or need
  repair before you spend a real run on them. See
  [Diagnostics](docs/sphinx/diagnostics.md) for what each check proves.

## What success looks like

A successful first run produces two concrete signals you can read the
morning after.

### `ralph --diagnose` should report all-healthy before you start

After step 3 in [Exact first steps](#exact-first-steps), the pre-flight
report should show every line green, with no missing, degraded, or
needs-repair signals. The verbatim healthy-output sample lives in the
repo-root [START_HERE.md](../START_HERE.md) — this page defers to it
so the pre-flight signal is described once, not four times across the
docs tree. If a line is red, `--diagnose` tells you what is missing
(e.g. an agent CLI not on `PATH`), unreachable (e.g. an MCP upstream),
or degraded (e.g. a capability whose provider key is unset). The full
failure-mode table is in [Diagnostics](docs/sphinx/diagnostics.md).

### A successful run leaves a finish-receipt you can review

After step 4 returns, you should find a `development_result` artifact
that names the change, the checks, and the reviewer focus without
reconstructing the run. The canonical verbatim receipt is in the
root [`README.md`](../README.md) — root README is the single source
for the empty-name-validation receipt block ("What a run leaves you"
section). A successful run leaves a short artifact you can read in
under a minute: outcome, changed files, checks, reviewer focus.

## Where to go next

- [Quickstart](docs/sphinx/quickstart.md) — short repeat-use reference
  with commands and flags
- [Getting Started](docs/sphinx/getting-started.md) — fuller first-run
  walkthrough with task guidance
- [Diagnostics](docs/sphinx/diagnostics.md) — what `ralph --diagnose`
  actually checks
- [Agent CLI lifecycle](docs/sphinx/agents.md) — selection,
  authentication, and invocation for every supported agent
- [Configuration](docs/sphinx/configuration.md) — config files and
  precedence
- [CLI reference](docs/sphinx/cli.md) — every flag and sub-command
- [Troubleshooting](docs/sphinx/troubleshooting.md) — when something
  goes wrong
- [After your first run](docs/sphinx/after-your-first-run.md) — turn a
  real run into a Codeberg signal
- [Docs map](../docs/README.md) — the docs router (routes by intent)
