# Ralph Workflow

> **Write the spec. Wake up to working software.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

Ralph Workflow is an agent-agnostic orchestration CLI for spec-driven planning, analysis, coding, and review. You write the task in `PROMPT.md`, Ralph routes each phase through the agents you choose, and you come back to a real codebase: changes in git, logs, artifacts, and review output you can inspect in your normal engineering process.

The point is not to generate a throwaway demo. Ralph Workflow is built for the kind of work that should leave you with a serious starting point for production: a working feature, a refactor in progress, a verified batch of tests, or a reviewable implementation foundation your team can keep pushing forward.

**Important first-run expectation:** Ralph Workflow is free and open source, but it does **not** replace the coding agents you already use. Before your first run, have at least one supported agent CLI already installed and already authenticated on your own machine. Ralph orchestrates that agent and hands back a reviewable result.

## Quick answer: is this for you?

- **What is it?** A free and open-source orchestration CLI that runs the coding agents you already use on your own machine.
- **Who is it for?** Developers and technical teams with work too big to babysit and too risky to trust blindly.
- **Why is it different?** It is repo-native and built to hand back a reviewable result — diff, checks, artifacts, and traceable reasoning — not just a transcript and a claim that the task is done.
- **Why use it now?** You can install it for free, hand off one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## Start here in the path that matches your question

- **I want the fastest honest first run.** Read [START_HERE.md](./START_HERE.md).
- **I want help choosing the first agent path.** Read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).
- **I want copy-paste prompt shapes for a real first task.** Read [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).
- **I am not sure whether my task is a good fit for unattended execution.** Read [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md).
- **I already use worktrees or multiple agent sessions. What does Ralph add?** Read [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md).
- **I already use Claude Code and want to know why I would add Ralph at all.** Read [docs/ralph-workflow-vs-claude-code.md](./docs/ralph-workflow-vs-claude-code.md).
- **I already use Claude Code + Codex together. What changes with Ralph?** Read [docs/claude-code-codex-workflow.md](./docs/claude-code-codex-workflow.md).
- **I run multiple agents already. What actually breaks first?** Read [docs/what-breaks-first-with-multiple-coding-agents.md](./docs/what-breaks-first-with-multiple-coding-agents.md).
- **I want a concrete merge-review path for AI output.** Read [docs/review-ai-coding-output-before-merge.md](./docs/review-ai-coding-output-before-merge.md).
- **I want to know what a strong morning-after handoff should actually contain.** Read [docs/what-a-good-ai-coding-finish-receipt-looks-like.md](./docs/what-a-good-ai-coding-finish-receipt-looks-like.md).
- **I already use Aider. Why would I use Ralph instead?** Read [docs/ralph-workflow-vs-aider.md](./docs/ralph-workflow-vs-aider.md).
- **I want to inspect the project where I already follow open-source tools.** Review the [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow) or the [primary Codeberg repo](https://codeberg.org/RalphWorkflow/Ralph-Workflow).
- **I want to see what a good result looks like before I try it.** Read [docs/free-open-source-proof.md](./docs/free-open-source-proof.md) for a concrete morning-after review path.

If you already judge open-source tools on GitHub, open the mirror first. If Ralph looks like a fit for your workflow, **star or watch it there before your first run** so you can track updates while you test it on a real backlog task.

## Want the longer argument before you install?

If you are still deciding what Ralph Workflow is for, whether unattended coding is a good fit, or how to judge the result honestly, these are the best deeper reads right now:

- [How to Tell if an AI Coding Task Is Actually Done](https://write.as/7pqpd2y0v0re2.md) — trust the finish line, not the model's confidence.
- [Claude Code + Codex Workflow: Plan, Build, Review](https://write.as/vesqh0lzrm4en.md) — a practical phase-split workflow for people already using both tools.
- [When Unattended AI Coding Actually Works](https://write.as/x5wil6pmtbvo1.md) — when to use an overnight run, and when not to.

They all point back to the same free/open-source evaluation path: use the agents you already have on your own machine, run one real backlog task tonight, and ask tomorrow whether you would merge the result.

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
If you are unsure which agent path to use first, read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).
If you want copy-paste starter specs instead of a blank page, read [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).
If you are not sure whether your task is a good fit for unattended execution, read [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md).
If you already use worktrees and want to understand what Ralph adds beyond isolation, read [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md).
If you already use Claude Code and want the clearest answer to “why not just stay in Claude Code?”, read [docs/ralph-workflow-vs-claude-code.md](./docs/ralph-workflow-vs-claude-code.md).
If you already use Claude Code and Codex together and want the cleanest role split, read [docs/claude-code-codex-workflow.md](./docs/claude-code-codex-workflow.md).
If you already run multiple agents and the review/reconstruction step is what hurts, read [docs/what-breaks-first-with-multiple-coding-agents.md](./docs/what-breaks-first-with-multiple-coding-agents.md).
If the part you do not trust yet is the morning-after merge decision itself, read [docs/review-ai-coding-output-before-merge.md](./docs/review-ai-coding-output-before-merge.md).
If you want the clearest picture of what a short trustworthy handoff should say before you merge, read [docs/what-a-good-ai-coding-finish-receipt-looks-like.md](./docs/what-a-good-ai-coding-finish-receipt-looks-like.md).
If you already like Aider for interactive work and want to know when Ralph is the better fit, read [docs/ralph-workflow-vs-aider.md](./docs/ralph-workflow-vs-aider.md).

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

See [docs/free-open-source-proof.md](./docs/free-open-source-proof.md) for a concrete first-task example, artifact bundle, and morning-after review path.
See [docs/review-ai-coding-output-before-merge.md](./docs/review-ai-coding-output-before-merge.md) if the part you want to judge first is whether the handoff is actually mergeable.
See [docs/what-a-good-ai-coding-finish-receipt-looks-like.md](./docs/what-a-good-ai-coding-finish-receipt-looks-like.md) if the main trust gap is still "what changed, what passed, and what still needs my judgment?"
See [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md) if the only thing blocking you is choosing the first agent path.
See [docs/ralph-workflow-vs-claude-code.md](./docs/ralph-workflow-vs-claude-code.md) if Claude Code is already your default and you want the sharpest contrast before trying Ralph.
See [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md) for copy-paste starter specs you can adapt tonight.
See [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md) for a simple good-task vs bad-task decision guide.
See [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md) for the practical difference between simple workspace isolation and a reviewable unattended handoff.
See [docs/what-breaks-first-with-multiple-coding-agents.md](./docs/what-breaks-first-with-multiple-coding-agents.md) if you already run parallel agents and want the clearest explanation of where trust actually breaks.
See [docs/ralph-workflow-vs-aider.md](./docs/ralph-workflow-vs-aider.md) if your current baseline is interactive AI pair programming and you want the clearest contrast.

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
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow> — inspect, star, or watch Ralph Workflow where you already follow open-source tools
- **PyPI package:** <https://pypi.org/project/ralph-workflow/>

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs. Use it commercially. Use it privately. Use it however you want.
