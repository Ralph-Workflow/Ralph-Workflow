# Ralph Workflow

> **Write the spec. Wake up to working software.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> Use this GitHub mirror if GitHub is where you already track open-source tools, but treat Codeberg as the main home: <https://github.com/Ralph-Workflow/Ralph-Workflow>

Ralph Workflow is an agent-agnostic orchestration CLI for spec-driven planning, analysis, coding, and review. You write the task in `PROMPT.md`, Ralph routes each phase through the agents you choose, and you come back to a real codebase: changes in git, logs, artifacts, and review output you can inspect in your normal engineering process.

The point is not to generate a throwaway demo. Ralph Workflow is built for the kind of work that should leave you with a serious starting point for production: a working feature, a refactor in progress, a verified batch of tests, or a reviewable implementation foundation your team can keep pushing forward.

**Important first-run expectation:** Ralph Workflow is free and open source, but it does **not** replace the coding agents you already use. Before your first run, have at least one supported agent CLI already installed and already authenticated on your own machine. Ralph orchestrates that agent and hands back a reviewable result.

## The shortest Codeberg conversion path after a real first run

Do not leave the evaluation private.

After one real run, convert the outcome into exactly one public action on **Codeberg**:

- **Promising run?** Star or watch the primary repo: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Shaky run or missing proof?** Open the right issue form on Codeberg: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Need the two-minute scorecard first?** Use [docs/after-your-first-run.md](./docs/after-your-first-run.md)

That is the clearest path from “this might be useful” to a real primary-repo trust signal.

## Start on Codeberg first

If Ralph Workflow looks like a fit, use the primary repo as the real next step:

- **Inspect the source on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Star / watch / fork on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Open issues or review the roadmap on Codeberg:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues>
- **Use the GitHub mirror only if GitHub is where you already follow projects:** <https://github.com/Ralph-Workflow/Ralph-Workflow>

That keeps adoption, issues, and review traffic pointed at the primary repo instead of splitting trust signals across two homes.

## Quick answer: is this for you?

- **What is it?** A free and open-source orchestration CLI that runs the coding agents you already use on your own machine.
- **Who is it for?** Developers and technical teams with work too big to babysit and too risky to trust blindly.
- **Why is it different?** It is built to hand back a reviewable result — diff, checks, artifacts, and traceable reasoning — not just a transcript and a claim that the task is done.
- **Why use it now?** You can install it for free, hand off one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## The shortest evaluator path most people actually need

If you only want the shortest honest path before you decide whether Ralph Workflow is worth your time, use these in order:

1. **Inspect the primary repo on Codeberg first** — <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. **Choose one real first task** — [docs/first-task-guide.md](./docs/first-task-guide.md)
3. **Run the shortest practical first run** — [START_HERE.md](./START_HERE.md)
4. **Judge the handoff** — [docs/reviewable-output.md](./docs/reviewable-output.md)
5. **Turn the result into a Codeberg action** — [docs/after-your-first-run.md](./docs/after-your-first-run.md)

If the blank page is the blocker, steal a starter spec from [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).

Everything else in this README is there to answer a specific objection. The main evaluation path is still simple: inspect the Codeberg repo, choose one bounded backlog task, run it, and ask whether you would merge the result.

## What you should get back tomorrow morning

If Ralph Workflow is doing its job, the morning-after handoff should look reviewable before you read a single long log:

```text
Task: Add empty-project-name validation to the CLI create flow

Changed files:
- cli/create.py
- tests/test_create.py

Checks run:
- unit tests for create flow
- lint / formatting checks if applicable

Open questions:
- should reserved names be rejected too?
- should whitespace be trimmed before validation?
```

That is the real promise: not “the agent seemed smart,” but **a bounded diff, checks that actually ran, and a clear merge decision**.

If you want the fuller artifact shape before you install, inspect the public [example review bundle](./docs/example-review-bundle.md).

If you try a real first run and anything feels unclear, shaky, or harder than it should, report it on **Codeberg** with the matching first-run or docs/proof form: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>. That is the fastest way to improve the real adoption path without splitting feedback across the mirror.

If the run goes well, do the public next step on **Codeberg** instead of leaving the evaluation private: [docs/after-your-first-run.md](./docs/after-your-first-run.md).

## If one specific blocker is still stopping you

If you already understand the main evaluation path, stop here and run it.

If you still need one deeper read, choose the single closest match:

- task choice: [docs/first-task-guide.md](./docs/first-task-guide.md)
- fastest first run: [START_HERE.md](./START_HERE.md)
- handoff standard: [docs/reviewable-output.md](./docs/reviewable-output.md)
- agent choice: [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md)

If you need more than that, use the docs index: [docs/README.md](./docs/README.md).

## Tonight's first run in five minutes

If you want the shortest honest test, do this in a real repo you already care about:

Prerequisites before you start:

- Python 3.12+
- a git repo you can safely test in
- at least one supported agent CLI already installed and already authenticated (for example Claude Code, Codex CLI, or OpenCode)

If you are unsure which one to start with, use the one already working on your machine and read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).

```bash
pipx install ralph-workflow
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

Paste a spec this small into `PROMPT.md`:

```markdown
# Goal

Add validation so the CLI rejects empty project names before creating files.
Keep the rest of the flow unchanged.

## Acceptance criteria

- Empty or whitespace-only project names fail with a clear error
- No project files are created for invalid names
- Existing valid-name behavior stays unchanged
- Tests cover the new validation
```

Then come back and ask one question:

> **Would I merge this?**

If yes, Ralph Workflow is useful for your codebase. If not, tighten the spec or task choice and run again.

## Why teams use Ralph Workflow

- **Write a spec, not a babysitting script.** Define the task and acceptance criteria once, then let the run continue without constant prompting.
- **Wake up to reviewable output.** Ralph leaves behind code changes, run logs, artifacts, and agent review instead of a giant chat transcript.
- **Use the agents you already have.** Route different phases through Claude Code, Codex CLI, OpenCode, or your preferred setup — for example, Claude for planning, an OpenCode-backed GPT model for analysis, and a different coding agent for development or review.
- **Keep the workflow in the repo.** Prompts, config, and run artifacts live with the codebase instead of disappearing into a hosted black box.
- **Aim past prototypes.** The best fit is work that should produce a strong implementation head start, not just a mockup.

## Install

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

### pipx

```bash
pipx install ralph-workflow
ralph --help
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
pip install -e ".[dev]" # or
make install # alternative to pip install
ralph --version
```

Requires Python 3.12+.

## Before your first run

Make sure the agent CLIs you want Ralph to call are already installed and already authenticated. Ralph Workflow does not manage provider login state or touch your credentials; you authenticate those tools first, and then Ralph invokes them directly and supervises the run. That makes phase-by-phase routing practical: you can keep one agent on planning, another on analysis, and another on coding or review without changing how the workflow is operated.

## Get it running

```bash
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

What to do in that flow:

1. **`ralph --init`** seeds the project-local `.agent/` files.
2. **`ralph --diagnose`** checks that your configured agents and MCP setup are reachable before you spend time on a real run.
3. **`PROMPT.md`** should describe one concrete task with clear acceptance criteria.
4. **`ralph`** directly invokes your configured agent CLIs and supervises the unattended run.

## Start with one real task

The best first test is not a vague demo.

Pick one real backlog task and use Ralph Workflow on that.

Good first tasks:
- a bounded feature slice
- a narrow refactor with tests
- a known cleanup task with clear checks
- repetitive implementation work where "done" is easy to judge

Bad first tasks:
- vague product exploration
- risky production surgery
- anything where nobody agrees what success looks like

The evaluation question is simple:

> **Would you merge this result?**

If yes, Ralph Workflow is doing useful work. If not, tighten the task, verification, or phase routing and try again.

For a practical walkthrough, read [START_HERE.md](./START_HERE.md).
If you need one deeper answer after that, use [docs/README.md](./docs/README.md) and choose the single closest page instead of skimming a long list from the README.

## What a good run feels like

You give Ralph Workflow a bounded product or engineering spec. It plans the work, hands implementation to the configured agents, runs review inside the workflow, and leaves you with something concrete to inspect afterward.

That usually means one of these outcomes:

- a working feature skeleton with the hard parts already wired up
- a serious implementation draft your team can refine toward production
- a verified batch of tests, docs, or refactors completed without live supervision
- a failed run with artifacts and logs that make the failure obvious instead of mysterious

## What good output looks like

A useful unattended run should not just say it is done.

It should leave you with:
- a real diff
- changed files you can inspect
- checks that actually ran
- clear notes about what changed
- open questions where uncertainty remains

If you want the short handoff standard, read [docs/reviewable-output.md](./docs/reviewable-output.md).
If you still have a trust-gap after that, use [docs/README.md](./docs/README.md) and pick the single page that matches it.

## Depth presets

```bash
ralph -Q     # quick: small fixes, single iteration
ralph        # standard: most features and tasks
ralph -T     # thorough: complex refactors, ten iterations
```

## When Ralph Workflow fits

- Multi-step coding tasks that do not fit in one prompt
- Work you want to hand off and review later
- Teams that want repeatable AI execution in the repo
- Runs where you want to mix stronger and cheaper models by phase
- Tasks where "come back to working software" is a better mental model than "chat with an agent"

## When it does not fit

- One-shot interactive prompts
- Pair-programming sessions where you want constant steering
- Tiny tasks that finish before setup overhead pays off
- Workflows that depend on unpredictable mid-run human input

## Where the name comes from

Ralph Workflow builds on the original Ralph idea: repeat a strong prompt until the model can make real progress. That loop was simple and powerful. In practice, Ralph Workflow is the Ralph loop on steroids: planning before implementation, verification after development, agent fallbacks, agent-agnostic execution, and customizable pipelines so unattended runs keep moving and teams can review the results with confidence.

## Need the deeper technical details?

Keep this README for onboarding. Use these when you want the full reference:

- **Product site:** <https://ralphworkflow.com>
- **Docs:** <https://ralphworkflow.com/docs>
- **Maintained Sphinx docs:** [`ralph-workflow/docs/sphinx/`](ralph-workflow/docs/sphinx/)
- **Package reference README:** [`ralph-workflow/README.md`](ralph-workflow/README.md)
- **Python contributor workflow:** [`ralph-workflow/CONTRIBUTING.md`](ralph-workflow/CONTRIBUTING.md)

## Links

- **Homepage:** <https://ralphworkflow.com>
- **Docs:** <https://ralphworkflow.com/docs>
- **Issues:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Repository:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Primary Codeberg repo:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow> — inspect, star, or watch Ralph Workflow on the main repo
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow> — follow the mirror if GitHub is where you already track open-source tools
- **PyPI package:** <https://pypi.org/project/ralph-workflow/>

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs. Use it commercially. Use it privately. Use it however you want.
