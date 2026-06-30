<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: clarified the role of the package-side START_HERE.md as
    the package-operator first-run path for someone who installed via
    pipx and is reading the package directory. The autopilot positioning
    and install/ralph --init/first-run sequence match the top-level
    README and the repo-root START_HERE so the three surfaces reinforce
    the same product story.
  - Why it belongs here: the page lives inside `ralph-workflow/`, which
    is the package directory a pipx-installed reader explores after
    `ralph --help`. The role boundary (package operator vs. repo
    visitor) keeps the route coherent instead of duplicating the
    repo-root START_HERE.
  - What was pruned, merged, or explicitly left alone: redundant
    long-form prose is trimmed; the rubric-aligned page-family minimum
    structure (evaluator kind → one realistic first-run goal →
    prerequisites → exact first steps → success signal → next click)
    is preserved. Quickstart / getting-started / agents / and
    configuration next-click chain stays.
  - How duplication was reduced or contained: this page is the
    package-side twin of the repo-root START_HERE — same sequence, same
    diagnostics pre-flight, but framed for a reader who has already
    installed and wants the operator fast-path. Both surfaces share the
    same ordered first-run sequence — `cd <repo>` → `ralph --init` →
    `ralph --diagnose` → `$EDITOR PROMPT.md` → `ralph` — matching the
    top-level README so a reader landing on either START_HERE sees the
    same install + first-run path.
  - How the route is clearer now than before: explicit role boundary up
    front, then prerequisites, then exact steps, then a success signal,
    then a short next-click chain into the operator manual.
-->

# Start Here: first run as a package operator

> **Codeberg is the primary repo.** Star, watch, and report issues there:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror.

Ralph Workflow is **the autopilot for coding agents** — a free and
open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes powerful
through composition.

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

After a good first run, you should be able to point to:

- A real repo change that matches the written task
- Meaningful checks that ran and reported clear outcomes
- A `development_result` artifact you can review without reconstructing
  the whole run
- A clear sense of whether the default workflow helped enough to keep
  using

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
