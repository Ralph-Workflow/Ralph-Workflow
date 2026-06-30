<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: rewrote the opening paragraph so the page leads with the
    canonical autopilot positioning language instead of the previous
    "composable loop framework" lead category. The phrase still appears
    later as a descriptive detail (the "composable" qualifier is true and
    informative), but no longer fronts the page.
  - Why it belongs here: this is a Sphinx manual how-to comparison page
    read alongside the autopilot-positioned README and the manual home.
    When the lead category differed, the page fought the rest of the
    manual (rubric hard failure: "README, START_HERE, docs map, and
    manual fight each other or duplicate each other").
  - What was pruned, merged, or explicitly left alone: the rest of the
    page's practical comparison argument (single Ralph-loop at the
    center, the comparison question, the default-workflow promise) is
    preserved with minor rewording so the comparison framing still
    carries the page.
  - How duplication was reduced or contained: the autopilot positioning
    is not restated at length — only the canonical first sentence and
    the value-prop / default-workflow sentence are reused from the
    top-level README so the manual stays coherent with the storefront.
  - How the route is clearer now than before: the page now opens with
    the same product category the reader sees in the README and the
    manual home, then narrows immediately into the comparison argument
    that gives the page its reason to exist.
-->

# AI Agent Orchestration CLI: What Matters in Practice

Ralph Workflow is **the autopilot for coding agents** — a free and
open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes
powerful through composition. **Hand it a well-specified coding task,
let the agents plan, build, verify, and fix, and come back to
reviewable, tested work.** The default workflow is strong enough to
adopt as-is, before you customize anything.

What does this mean in practice when you compare AI agent
orchestration CLIs? Not a thin wrapper, but a **composable loop
framework** that runs the coding agents you already use on your own
machine. Its opinion is simple: the orchestrator should not be more
complex than the work it is orchestrating. A single understandable
Ralph-loop at the center composes into more ambitious workflows
without the CLI turning into a maze of flags and phase names you
need a diagram to follow.

If you are comparing AI agent orchestration CLIs, the useful question
is not whether a tool can call an agent.
The useful question is whether it gives you a workflow that stays
understandable, reviewable, and extensible when the task stops being
tiny.

## What Ralph Workflow is trying to solve

A single long coding-agent session can work for small edits.
It gets much shakier when the task needs:

- a real written spec
- explicit planning before implementation
- repeated verification instead of one final guess
- room to swap or extend agent behavior later
- a handoff a human can judge without reverse-engineering the whole run

Ralph Workflow takes the simple Ralph-loop idea and uses it as the center of a larger orchestration model.
The point is not complexity for its own sake.
The point is to keep the center simple so the larger workflow stays easier to reason about.

## Why the default workflow matters

The default workflow matters because most users should not have to design an orchestration system before they can test one.
You should be able to start with the shipped path, run a real task, and only then decide whether to extend it.

That is the practical promise: simple at the center, stronger in composition, useful before customization.

## Where to go next

- for the shortest honest first run: [START_HERE.md](../START_HERE.md)
- for task selection help: [first-task-guide.md](./first-task-guide.md)
- for the operator manual: [Sphinx manual home](./index.rst)
- for configuration and file locations: [configuration.md](./configuration.md)
