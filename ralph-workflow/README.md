# Ralph Workflow (Python)

> **The operating system for autonomous coding.**
>
> **GitHub is the mirror. Codeberg is the primary repo.**
> Inspect, star, watch, and open issues on Codeberg first: <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> Use the GitHub mirror only as a secondary follow/read surface if GitHub is where you already track projects: <https://github.com/Ralph-Workflow/Ralph-Workflow>

Ralph Workflow is a **free and open-source** Python 3.12+ CLI for **AI agent orchestration** on your own machine.

It extends the simple Ralph loop into a **composable loop framework** for real software engineering: planning, implementation, verification, review, and agent routing inside one repo-native workflow.

The default workflow shipped with Ralph Workflow is already strong for writing software. You can use it as-is, or compose more complex workflows when one loop is not enough.

Use it for engineering tasks that are **too big to babysit and too risky to trust blindly**.

Why use it now? Because you can install it in minutes, hand it one real backlog task tonight, and wake up to **completed code, real checks, or an honest blocked state**.

**Start here:**

- [Try Ralph Workflow on one real backlog task](START_HERE.md)
- [Choose Your First Ralph Workflow Task](docs/first-task-guide.md)
- [Getting Started with Ralph Workflow](docs/sphinx/getting-started.md)
- [Quickstart reference](docs/sphinx/quickstart.md)
- [Sphinx docs source](docs/sphinx/README.md)
- [AI Agent Orchestration CLI](docs/ai-agent-orchestration-cli.md)
- [Which Agent Should I Start With?](docs/sphinx/which-agent-should-i-start-with.md)
- [Ralph Workflow vs Claude Code](docs/sphinx/ralph-workflow-vs-claude-code.md)
- [Ralph Workflow vs OpenCode](docs/ralph-workflow-vs-opencode.md)
- [After Your First Run](docs/after-your-first-run.md)

Ralph Workflow supports mixed-agent runs across planning, analysis, development, review, and commit phases. You might plan with Claude, route analysis through an OpenCode-backed GPT model, hand development to Codex or another OpenCode provider, and keep review on a different agent chain — all inside the same repo-native workflow.

## What makes Ralph Workflow different

Most agent tools help you run one coding session.

Ralph Workflow is for when you need more than that:

- a **default workflow** that is already good at writing software
- **composable loops** instead of a single long session
- **agent orchestration** across phases instead of one tool doing everything
- **repo-native execution** instead of hidden product state
- **real verification** as part of the workflow, not an afterthought

This is why Ralph Workflow is not just “another agent wrapper.” It is an orchestration system built to scale the simple Ralph Workflow loop idea into real software workflows.

## A fast way to tell whether Ralph Workflow fits

1. Pick one real substantial backlog task with a defined product outcome. If you want the filter first, use [Choose Your First Ralph Workflow Task](docs/first-task-guide.md).
2. Write it down in `PROMPT.md` with a detailed product or engineering spec and clear acceptance criteria.
3. Run Ralph Workflow overnight.
4. Come back and check: did it produce working code, real verification, or an honest blocked state?

If yes, give it a harder task next.
If no, tighten the spec, checks, or task choice and run again.

If the first run teaches you something real either way, turn that result into the right public Codeberg action with [After Your First Run](docs/sphinx/after-your-first-run.md): star/watch the primary repo if it earned trust, or report the exact first-run friction on Codeberg if it did not.

## Proof before you install

If you want the fastest trust check before a first run, open the public [Example Review Bundle](docs/sphinx/example-review-bundle.md).
It shows the exact kind of morning-after handoff Ralph Workflow is trying to produce: a real `PROMPT.md`, result notes, review feedback, fix notes, and artifact files you can inspect before deciding whether to try Ralph Workflow on your own backlog.

If your real question is "what should a good finished run actually prove?", read [What Good Ralph Workflow Output Looks Like](docs/reviewable-output.md) before you install.

## What you get

- **Spec-driven unattended runs** for real engineering work such as refactors, test generation, documentation sweeps, and migrations
- **Repo-native workflow files** instead of hidden product state
- **Composable loops** built for real software work instead of one-shot agent sessions
- **Flexible agent routing** across Claude Code, Codex CLI, OpenCode, and your own configured agents
- **Phase-by-phase model selection** so planning, analysis, development, review, and commit can each use the best-fit agent chain
- **A practical default workflow** you can use before inventing anything custom
- **Completed code, real checks, or an honest blocked state** instead of vague “done” claims

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

Ralph Workflow builds on the original Ralph idea: repeat a strong loop until the model can make real progress. That core idea stays simple. The difference is that Ralph Workflow turns it into a **composable orchestration system**: planning before implementation, verification after development, agent fallbacks, agent-agnostic execution, and customizable pipelines that can scale from one strong default software workflow to much more complex autonomous engineering runs.

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

## Third-party places to inspect Ralph Workflow

- **ToolWise review page:** <https://toolwise.ai/tools/ralph-workflow>
- **SaaSHub product page:** <https://www.saashub.com/ralph-workflow>
- **TechTools Launchpad listing:** <https://techtools.cz/tools/launchpad/?tool=71>

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs. Use it commercially. Use it privately. Use it however you want.
