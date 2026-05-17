# Ralph Workflow

> **Write the spec. Wake up to working software.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Ralph Workflow is an agent-agnostic orchestration CLI for spec-driven planning, analysis, coding, and review. You write the task in `PROMPT.md`, Ralph routes each phase through the agents you choose, and you come back to a real codebase: changes in git, logs, artifacts, and review output you can inspect in your normal engineering process.

The point is not to generate a throwaway demo. Ralph Workflow is built for the kind of work that should leave you with a serious starting point for production: a working feature, a refactor in progress, a verified batch of tests, or a reviewable implementation foundation your team can keep pushing forward.

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
If you are not sure whether your task is a good fit for unattended execution, read [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md).
If you already use worktrees and want to understand what Ralph adds beyond isolation, read [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md).

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

See [docs/free-open-source-proof.md](./docs/free-open-source-proof.md) for a concrete first-task example and review bundle example.
See [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md) for a simple good-task vs bad-task decision guide.
See [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md) for the practical difference between simple workspace isolation and a reviewable unattended handoff.

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
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
- **PyPI package:** <https://pypi.org/project/ralph-workflow/>

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs. Use it commercially. Use it privately. Use it however you want.
