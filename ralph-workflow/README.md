# Ralph Workflow (Python)

> **Write the spec. Wake up to reviewable output.**
>
> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> Use the GitHub mirror only as a secondary follow/read surface if GitHub is where you already track projects: <https://github.com/Ralph-Workflow/Ralph-Workflow>

Ralph Workflow is a **free and open-source** Python 3.12+ CLI that orchestrates the coding agents you already use **on your own machine** for substantial unattended work.

It is for developers and technical teams with engineering tasks that are **too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding chat is the handoff: Ralph Workflow keeps the workflow in your repo, runs planning + implementation + review as one unattended pass, and leaves you with **code changes, logs, artifacts, and review context you can inspect in your normal git workflow**.

Why use it now? Because you can install it in minutes, hand it one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

**Start here:**

- [Try Ralph Workflow on one real backlog task](START_HERE.md)
- [After Your First Run](docs/after-your-first-run.md) — shortest Codeberg-first scorecard after a real evaluation
- [Choose Your First Ralph Workflow Task](docs/first-task-guide.md) — fastest honest filter for what to run first
- [Getting Started with Ralph Workflow](docs/sphinx/getting-started.md)
- [Quickstart reference](docs/sphinx/quickstart.md)
- [AI Agent Orchestration CLI](docs/ai-agent-orchestration-cli.md) — practical repo-native path if you are comparing orchestration CLIs
- [Spec-Driven AI Agent](docs/spec-driven-ai-agent.md) — practical repo-native path if your real question is spec-first execution
- [Claude Code Automation](docs/claude-code-automation.md) — practical repo-native path if your real question is Claude Code automation
- [Claude Code Approval Mode](docs/claude-code-approval-mode.md) — practical repo-native path if approval mode still turns "autonomous" work into transcript babysitting
- [Which Agent Should I Start With?](docs/sphinx/which-agent-should-i-start-with.md) — choose the agent already installed and authenticated on your machine
- [Ralph Workflow vs Claude Code](docs/sphinx/ralph-workflow-vs-claude-code.md) — decide when an interactive Claude session is enough and when an unattended reviewable handoff is better
- [Ralph Workflow vs Aider](docs/sphinx/ralph-workflow-vs-aider.md) — decide when interactive pair-programming is enough and when an unattended morning-after handoff is better
- [Ralph Workflow vs OpenCode](docs/ralph-workflow-vs-opencode.md) — decide when direct provider flexibility is enough and when a reviewable unattended handoff is better
- [Claude Code + Codex Workflow](docs/sphinx/claude-code-codex-workflow.md) — keep the role split, but come back to a cleaner reviewable handoff
- [What a Good AI Coding Finish Receipt Looks Like](docs/sphinx/what-a-good-ai-coding-finish-receipt-looks-like.md) — the short morning-after handoff that should tell you what changed, what passed, and what still needs judgment
- [Example Review Bundle](docs/sphinx/example-review-bundle.md) — inspect a public sample prompt, result notes, review feedback, and artifacts before your own first run

Ralph Workflow supports mixed-agent runs across planning, analysis, development, review, and commit phases. You might plan with Claude, route analysis through an OpenCode-backed GPT model, hand development to Codex or another OpenCode provider, and keep review on a different agent chain — all inside the same repo-native workflow.

## A fast way to tell whether Ralph Workflow fits

1. Pick one real backlog task that is small enough to review in one sitting. If you want the filter first, use [Choose Your First Ralph Workflow Task](docs/first-task-guide.md).
2. Write it down in `PROMPT.md` with clear acceptance criteria.
3. Run Ralph Workflow overnight.
4. Come back and ask one question: **would you merge this?**

If yes, give it a harder task next.
If no, tighten the spec, checks, or task choice and run again.

If the first run teaches you something real either way, turn that result into the right public Codeberg action with [After Your First Run](docs/sphinx/after-your-first-run.md): star/watch the primary repo if it earned trust, or report the exact first-run friction on Codeberg if it did not.

## Proof before you install

If you want the fastest trust check before a first run, open the public [Example Review Bundle](docs/sphinx/example-review-bundle.md).
It shows the exact kind of morning-after handoff Ralph Workflow is trying to produce: a real `PROMPT.md`, result notes, review feedback, fix notes, and artifact files you can inspect before deciding whether to try Ralph Workflow on your own backlog.

If your real question is "what should the morning-after handoff actually look like?", read [What Good Ralph Workflow Output Looks Like](docs/reviewable-output.md) before you install.

## What you get

- **Spec-driven unattended runs** for real engineering work such as refactors, test generation, documentation sweeps, and migrations
- **Repo-native workflow files** instead of hidden product state
- **Agent-reviewed output** instead of a long interactive transcript
- **Flexible agent routing** across Claude Code, Codex CLI, OpenCode, and your own configured agents
- **Phase-by-phase model selection** so planning, analysis, development, review, and commit can each use the best-fit agent chain
- **A practical default workflow** you can use before inventing anything custom

## Install

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

### pipx

```bash
python -m pip install pipx
python -m pipx ensurepath
pipx install ralph-workflow
ralph --help
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
pip install -e .
ralph --version
```

Requires Python 3.12+.

## Before your first run

1. Install the agent CLIs you want Ralph Workflow to call.
2. Authenticate those CLIs normally.
3. Pick one small, concrete task for the first run.

Ralph Workflow does not manage provider authentication or store your agent credentials. You authenticate the agent CLIs yourself first, and Ralph Workflow then invokes those tools directly and supervises the workflow, even when different phases are routed through different agent families.

## Quick start

```bash
cd /path/to/your/project
ralph --init
ralph --diagnose
$EDITOR PROMPT.md
ralph
```

What happens in that flow:

- **`ralph --init`** creates the local `.agent/` support files.
- **`ralph --diagnose`** checks whether your configured agents and MCP setup are reachable.
- **`PROMPT.md`** becomes the task spec for the run.
- **`ralph`** directly invokes your configured agent CLIs and starts the unattended workflow.

After `ralph --init`, review the generated `.agent/` support files. If this repository needs a project-local main-config override, run `ralph --init-local-config` to create `.agent/ralph-workflow.toml`, then point the workflow at the agent CLIs you already use for planning, development, and review.

## What to expect from a run

Ralph Workflow is meant to get you to a strong implementation starting point while you are away, not to replace engineering judgment.

A good run should leave you with:

- code that compiles, tests, or clearly shows where work remains
- review artifacts and logs that explain what happened
- a result that is worth continuing from, not discarding and restarting

That may be a finished small task, or it may be a substantial first pass toward production on a larger one.

## Good first tasks

Start with work that is easy to verify:

- add tests to an existing module
- fix known lint failures
- refactor one narrow subsystem
- update documentation backed by existing code

## Depth presets

```bash
ralph -Q     # quick: small fixes, single iteration
ralph        # standard: most features and tasks
ralph -T     # thorough: complex refactors, ten iterations
```

## When Ralph Workflow fits

- Multi-step tasks that outgrow one prompt
- Work you want to review after the fact instead of steering live
- Teams that want AI execution to stay in the repo
- Runs where you want to mix stronger and cheaper models by phase

## When it does not fit

- One-shot interactive prompts
- Pair-programming sessions with constant human steering
- Tiny tasks where setup overhead is not worth it
- Workflows that need unpredictable mid-run human input

## Where the name comes from

Ralph Workflow builds on the original Ralph idea: repeat a strong prompt until the model can make real progress. That loop was simple and powerful. In practice, Ralph Workflow is the Ralph loop on steroids: planning before implementation, verification after development, agent fallbacks, agent-agnostic execution, and customizable pipelines so unattended runs keep moving and teams can review the results with confidence.

## Development and verification

If you are changing Ralph Workflow itself, start with [`CONTRIBUTING.md`](CONTRIBUTING.md) and run the canonical verification command before you finish:

```bash
make verify
```

## Documentation

This README intentionally leaves out deeper implementation details and defers to the `docs/sphinx/` pages for those.

- **Quickstart:** [`docs/sphinx/quickstart.md`](docs/sphinx/quickstart.md) — shorter repeat-use reference with commands and flags
- **Getting Started:** [`docs/sphinx/getting-started.md`](docs/sphinx/getting-started.md) — fuller first-run walkthrough with task guidance
- **Concepts:** [`docs/sphinx/concepts.md`](docs/sphinx/concepts.md) — terminology and mental model
- **CLI Reference:** [`docs/sphinx/cli.md`](docs/sphinx/cli.md) — all flags and sub-commands
- **Configuration:** [`docs/sphinx/configuration.md`](docs/sphinx/configuration.md) — config files and precedence
- **Troubleshooting:** [`docs/sphinx/troubleshooting.md`](docs/sphinx/troubleshooting.md) — common failure modes and shortest fixes
- **Developer Reference:** [`docs/sphinx/developer-reference.md`](docs/sphinx/developer-reference.md) — internal architecture and extension points
- **Python API Reference:** [`docs/sphinx/modules.rst`](docs/sphinx/modules.rst) — package documentation
- **Documentation map:** [`docs/README.md`](docs/README.md) — repo-native evaluator doc map and package doc index
- **Website and full docs:** <https://ralphworkflow.com/docs>

## Links

- **Homepage:** <https://ralphworkflow.com>
- **Docs:** <https://ralphworkflow.com/docs>
- **Source repository (primary):** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
- **Issue tracker:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **PyPI package:** <https://pypi.org/project/ralph-workflow/>

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs. Use it commercially. Use it privately. Use it however you want.
