# Ralph Workflow

> **Write the spec. Wake up to working software.**

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, fork, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> Use this GitHub mirror if GitHub is where you already track open-source tools: <https://github.com/Ralph-Workflow/Ralph-Workflow>

Ralph Workflow is an agent-agnostic orchestration CLI for spec-driven planning, analysis, coding, and review. You write the task in `PROMPT.md`, Ralph routes each phase through the agents you choose, and you come back to a real codebase: changes in git, logs, artifacts, and review output you can inspect in your normal engineering process.

The point is not to generate a throwaway demo. Ralph Workflow is built for the kind of work that should leave you with a serious starting point for production: a working feature, a refactor in progress, a verified batch of tests, or a reviewable implementation foundation your team can keep pushing forward.

**Important first-run expectation:** Ralph Workflow does **not** replace the coding agents you already use. Before your first run, have at least one supported agent CLI already installed and already authenticated on your own machine. Ralph orchestrates that agent and hands back a reviewable result.

## The shortest Codeberg conversion path after a real first run

Do not leave the evaluation private.

After one real run, convert the outcome into exactly one public action on **Codeberg**:

- **Promising run?** Star or watch the primary repo: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **Shaky run or missing proof?** Open the right issue form on Codeberg: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Need the two-minute scorecard first?** Use [docs/after-your-first-run.md](./docs/after-your-first-run.md)

That is the clearest path from "this might be useful" to a real primary-repo trust signal.

## Quick answer: is this for you?

- **What is it?** A free and open-source orchestration CLI that runs the coding agents you already use on your own machine.
- **Who is it for?** Developers and technical teams with work too big to babysit and too risky to trust blindly.
- **Why is it different?** It is repo-native and built to hand back a reviewable result — diff, checks, artifacts, and traceable reasoning — not just a transcript and a claim that the task is done.
- **Why use it now?** You can install it for free, hand off one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

## The four links most evaluators actually need

If you only want the shortest honest path before you decide whether Ralph Workflow is worth your time, use these:

1. **Inspect the primary repo on Codeberg first** — <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
2. **Run one real first task with the shortest setup path** — [START_HERE.md](./START_HERE.md)
3. **Check the public proof asset before or after that run** — [docs/example-review-bundle.md](./docs/example-review-bundle.md)
4. **Use the first-run scorecard and turn the result into a Codeberg action** — [docs/after-your-first-run.md](./docs/after-your-first-run.md)

Everything else in this README is there to answer a specific objection. The main evaluation path is still simple: inspect the Codeberg repo, run one bounded backlog task, and ask whether you would merge the result.

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

That is the real promise: not "the agent seemed smart," but **a bounded diff, checks that actually ran, and a clear merge decision**.

If you want the fuller artifact shape before you install, inspect the public [example review bundle](./docs/example-review-bundle.md).

If you try a real first run and anything feels unclear, shaky, or harder than it should, report it on **Codeberg**: <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>. If the run goes well, do the public next step on **Codeberg** instead of leaving the evaluation private: [docs/after-your-first-run.md](./docs/after-your-first-run.md).

## Deeper paths only if one specific objection is blocking you

- **I want the fastest honest first run.** Read [START_HERE.md](./START_HERE.md).
- **I want help choosing the first agent path.** Read [docs/which-agent-should-i-start-with.md](./docs/which-agent-should-i-start-with.md).
- **I want copy-paste prompt shapes for a real first task.** Read [docs/first-task-prompt-templates.md](./docs/first-task-prompt-templates.md).
- **I am not sure whether my task is a good fit for unattended execution.** Read [docs/when-unattended-coding-fits.md](./docs/when-unattended-coding-fits.md).
- **I already use worktrees or multiple agent sessions. What does Ralph add?** Read [docs/why-worktrees-are-not-enough.md](./docs/why-worktrees-are-not-enough.md).
- **I use Claude Code and I am specifically looking for a stronger automation / unattended path.** Read [docs/claude-code-automation.md](./docs/claude-code-automation.md).
- **I want to run Claude Code overnight without babysitting the terminal.** Read [docs/run-claude-code-overnight-without-babysitting.md](./docs/run-claude-code-overnight-without-babysitting.md).
- **Claude Code approval mode or plan mode still leaves me babysitting the run.** Read [docs/claude-code-approval-mode.md](./docs/claude-code-approval-mode.md).
- **I already use Claude Code and want to know why I would add Ralph at all.** Read [docs/ralph-workflow-vs-claude-code.md](./docs/ralph-workflow-vs-claude-code.md).
- **I already use OpenCode and want to know when Ralph is the better fit.** Read [docs/ralph-workflow-vs-opencode.md](./docs/ralph-workflow-vs-opencode.md).
- **I already use Codex CLI and want to know when Ralph is the better fit.** Read [docs/ralph-workflow-vs-codex-cli.md](./docs/ralph-workflow-vs-codex-cli.md).
- **I already use Claude Code + Codex together. What changes with Ralph?** Read [docs/claude-code-codex-workflow.md](./docs/claude-code-codex-workflow.md).
- **I run multiple agents already. What actually breaks first?** Read [docs/what-breaks-first-with-multiple-coding-agents.md](./docs/what-breaks-first-with-multiple-coding-agents.md).
- **I want a concrete merge-review path for AI output.** Read [docs/review-ai-coding-output-before-merge.md](./docs/review-ai-coding-output-before-merge.md).
- **I want the sharpest owned answer to "how do I tell if the AI task is actually done?"** Read [docs/how-to-tell-if-an-ai-coding-task-is-actually-done.md](./docs/how-to-tell-if-an-ai-coding-task-is-actually-done.md).
- **I want to know what a strong morning-after handoff should actually contain.** Read [docs/what-a-good-ai-coding-finish-receipt-looks-like.md](./docs/what-a-good-ai-coding-finish-receipt-looks-like.md).
- **I want unattended runs to fail closed instead of drifting all night.** Read [docs/bounded-autonomy-for-unattended-coding.md](./docs/bounded-autonomy-for-unattended-coding.md).
- **I keep thinking I need remote supervision, but really I need a trustworthy finish state.** Read [docs/remote-supervision-of-coding-agents.md](./docs/remote-supervision-of-coding-agents.md).
- **I am looking for an open-source AI coding orchestrator I can inspect before I install.** Read [docs/open-source-ai-coding-orchestrator.md](./docs/open-source-ai-coding-orchestrator.md).
- **I am evaluating AI agent orchestration CLIs and want the practical difference.** Read [docs/ai-agent-orchestration-cli.md](./docs/ai-agent-orchestration-cli.md).
- **I am searching for an unattended coding agent I can actually trust overnight.** Read [docs/unattended-coding-agent.md](./docs/unattended-coding-agent.md).
- **I want a spec-driven AI agent, not just a bigger prompt loop.** Read [docs/spec-driven-ai-agent.md](./docs/spec-driven-ai-agent.md).
- **I already use Aider. Why would I use Ralph instead?** Read [docs/ralph-workflow-vs-aider.md](./docs/ralph-workflow-vs-aider.md).
- **I want to inspect the project where I already follow open-source tools.** Start with the [primary Codeberg repo](https://codeberg.org/RalphWorkflow/Ralph-Workflow), or use the [GitHub mirror](https://github.com/Ralph-Workflow/Ralph-Workflow) if that is where you already track projects.
- **I want to see what a good result looks like before I try it.** Read [docs/free-open-source-proof.md](./docs/free-open-source-proof.md) for a concrete morning-after review path.

## Independent places to inspect Ralph Workflow

- [GitDB project page](https://gitdb.net/Ralph-Workflow/Ralph-Workflow) — inspect the GitHub mirror from a GitHub-native discovery surface.
- [SaaSHub product page](https://www.saashub.com/ralph-workflow) — review the product card and alternatives context.
- [SaaSHub alternatives](https://www.saashub.com/ralph-workflow-alternatives) — compare Ralph Workflow against adjacent tools in the same evaluation flow.
- [TechTools Launchpad listing](https://techtools.cz/tools/launchpad/?tool=71) — a live developer-tools directory entry.

Those pages all point back to the same honest evaluation path: Ralph Workflow is a free and open-source way to orchestrate the coding agents you already use on your own machine for overnight work you can review in the morning.

## Tonight's first run in five minutes

If you want the shortest honest test, do this in a real repo you already care about:

Prerequisites before you start:

- Python 3.12+
- a git repo you can safely test in
- at least one supported agent CLI already installed and already authenticated (Claude Code, Codex CLI, or OpenCode)

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

## What good output looks like

A useful unattended run should not just say it is done.

It should leave you with:
- a real diff
- changed files you can inspect
- checks that actually ran
- clear notes about what changed
- open questions where uncertainty remains

See [docs/free-open-source-proof.md](./docs/free-open-source-proof.md) for a concrete first-task example, artifact bundle, and morning-after review path.

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
