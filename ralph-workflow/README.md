# Ralph Workflow (Python)

> **Write the spec. Wake up to reviewable output.**

Ralph Workflow is a **free and open-source** Python 3.12+ CLI that orchestrates the coding agents you already use **on your own machine** for substantial unattended work.

It is for developers and technical teams with engineering tasks that are **too big to babysit and too risky to trust blindly**.

What makes it different from a normal AI coding chat is the handoff: Ralph Workflow keeps the workflow in your repo, runs planning + implementation + review as one unattended pass, and leaves you with **code changes, logs, artifacts, and review context you can inspect in your normal git workflow**.

Why use it now? Because you can install it in minutes, hand it one real backlog task tonight, and decide tomorrow whether the result is something you would actually merge.

**Start here:**

- [Start Here: try Ralph Workflow on one real backlog task](START_HERE.md)
- [Already using Claude Code and wondering why you would add Ralph Workflow? Read the direct comparison](docs/sphinx/ralph-workflow-vs-claude-code.md)
- [Already using Claude Code or Codex? Pick the lowest-friction first agent path](docs/sphinx/which-agent-should-i-start-with.md)
- [Already splitting work across Claude Code + Codex? Use the cleaner handoff workflow](docs/sphinx/claude-code-codex-workflow.md)
- [Inspect a public example review bundle first](docs/sphinx/example-review-bundle.md)
- [Docs: Getting Started](docs/sphinx/getting-started.md)
- [Docs site](https://ralphworkflow.com/docs)
- Prefer GitHub? The public mirror stays in sync here: <https://github.com/Ralph-Workflow/Ralph-Workflow>

Ralph Workflow supports mixed-agent runs across planning, analysis, development, review, and commit phases. You might plan with Claude, route analysis through an OpenCode-backed GPT model, hand development to Codex or another OpenCode provider, and keep review on a different agent chain — all inside the same repo-native workflow.

The deeper transport and configuration details live in the reference docs; this README stays focused on how to get a real unattended workflow running quickly.

This package is a good fit when you want more than a demo. Ralph Workflow is designed for the kind of bounded engineering work that should leave you with a working feature, a verified refactor, a serious production-bound draft, or a reviewable implementation foundation.

## A fast way to tell whether Ralph Workflow fits

1. Pick one real backlog task that is small enough to review in one sitting.
2. Write it down in `PROMPT.md` with clear acceptance criteria.
3. Run Ralph Workflow overnight.
4. Come back and ask one question: **would you merge this?**

If yes, give it a harder task next.
If no, tighten the spec, checks, or task choice and run again.

## Proof before you install

If you want the fastest trust check before a first run, open the public [Example Review Bundle](docs/sphinx/example-review-bundle.md).
It shows the exact kind of morning-after handoff Ralph Workflow is trying to produce: a real `PROMPT.md`, result notes, review feedback, fix notes, and artifact files you can inspect before deciding whether to try Ralph Workflow on your own backlog.

If your real question is "which setup should I start with tonight?", use one of these paths instead of reading generic docs first:

- [Which Agent Should I Start With?](docs/sphinx/which-agent-should-i-start-with.md) — choose the agent already installed and authenticated on your machine.
- [Ralph Workflow vs Claude Code](docs/sphinx/ralph-workflow-vs-claude-code.md) — decide when an interactive Claude session is enough and when an unattended reviewable handoff is better.
- [Claude Code + Codex Workflow](docs/sphinx/claude-code-codex-workflow.md) — keep the role split, but come back to a cleaner reviewable handoff.

If you prefer to inspect, star, or watch open-source projects on GitHub, the synced mirror lives at <https://github.com/Ralph-Workflow/Ralph-Workflow>. The primary source of truth remains Codeberg, but you can follow Ralph Workflow from either place.

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

Use the website and docs for the deeper material this README intentionally leaves out:

- **Homepage:** <https://ralphworkflow.com>
- **Docs:** <https://ralphworkflow.com/docs>
- **Documentation map:** [`../docs/README.md`](../docs/README.md)
- **Maintained Sphinx docs:** [`docs/sphinx/`](docs/sphinx/)
- **Quickstart:** [`docs/sphinx/quickstart.md`](docs/sphinx/quickstart.md)
- **Developer reference:** [`docs/sphinx/developer-reference.md`](docs/sphinx/developer-reference.md)
- **Python API reference:** [`docs/sphinx/modules.rst`](docs/sphinx/modules.rst)
- **Source repository (primary):** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **GitHub mirror:** <https://github.com/Ralph-Workflow/Ralph-Workflow>
- **Issue tracker:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>

## License

[AGPL-3.0-or-later](LICENSE).

The framework is copyleft. The code Ralph Workflow generates belongs to you — no license encumbrance on outputs. Use it commercially. Use it privately. Use it however you want.
