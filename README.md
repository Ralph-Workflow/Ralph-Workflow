# Ralph Workflow

> **Write the spec. Wake up to working software.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Ralph Workflow is an agent-agnostic orchestration CLI for spec-driven planning, coding, and review. You write the task in `PROMPT.md`, Ralph runs the workflow unattended, and you come back to a real codebase: changes in git, logs, artifacts, and review output you can inspect in your normal engineering process.

The point is not to generate a throwaway demo. Ralph Workflow is built for the kind of work that should leave you with a serious starting point for production: a working feature, a refactor in progress, a verified batch of tests, or a reviewable implementation foundation your team can keep pushing forward.

## Why teams use Ralph Workflow

- **Write a spec, not a babysitting script.** Define the task and acceptance criteria once, then let the run continue without constant prompting.
- **Wake up to reviewable output.** Ralph leaves behind code changes, run logs, artifacts, and agent review instead of a giant chat transcript.
- **Use the agents you already have.** Route different phases through Claude Code, Codex CLI, OpenCode, or your preferred setup.
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

Make sure the agent CLIs you want Ralph to call are already installed and authenticated. Ralph Workflow reuses those existing CLIs instead of asking you to re-enter provider credentials into a separate product.

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
4. **`ralph`** starts the unattended run.

## What a good run feels like

You give Ralph Workflow a bounded product or engineering spec. It plans the work, hands implementation to the configured agents, runs review inside the workflow, and leaves you with something concrete to inspect afterward.

That usually means one of these outcomes:

- a working feature skeleton with the hard parts already wired up
- a serious implementation draft your team can refine toward production
- a verified batch of tests, docs, or refactors completed without live supervision
- a failed run with artifacts and logs that make the failure obvious instead of mysterious

## Good first tasks

Start with boring, bounded work:

- add tests to an existing module
- fix a known batch of lint failures
- refactor one narrow subsystem
- update docs that are backed by existing code

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
- **GitHub mirror:** <https://github.com/mistlight/Ralph-Workflow>
- **PyPI package:** <https://pypi.org/project/ralph-workflow/>

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs. Use it commercially. Use it privately. Use it however you want.
