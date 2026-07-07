# Ralph Workflow

> **Codeberg is primary.** Star, watch, fork, and report issues there first:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow> (verify: repo-exists)
> GitHub is a read-only mirror:
> <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

This page is the PyPI-facing README for the ralph-workflow package.

Ralph Workflow is a free, open-source Python CLI for running an
autonomous coding-agent loop. It runs the agents you already use — Claude
Code, Codex, OpenCode, Nanocoder, Google Anti Gravity, and Pi — on your
own machine, with your keys to yourself.

## What it is

Ralph Workflow is an **operating system for autonomous coding**: the
agents handle the long middle of engineering work while you handle the
judgment that only a human can make.

The simple Ralph-loop idea — plan, build, verify — becomes a
**composable loop framework** under the hood: each phase can loop
independently and hand off to the next, so a single `ralph` command
spawns planning, development iteration, review, and fix cycles across
multiple agents and then produces finished git commits you can review
when you come back.

## Who it's for

Ralph Workflow is for developers and small teams with engineering work
that is **too big to babysit and too risky to trust blindly** — the kind
of ambitious, well-specified work that you would trust a capable
colleague to do unattended.

It is **not** for one-line fixes, vague prompts, or repos without tests.
A repo without guardrails will produce results that reflect that.

## Install and run

The fastest PyPI install:

```bash
pipx install ralph-workflow
```

The full first-run path (install → init → write `PROMPT.md` → run →
review) is the single source of truth in the root
[`README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md) —
see the **"Start your first run"** section. PyPI readers should follow
the root README for the 6-step walkthrough; this README intentionally leaves out the deeper operator material to avoid drift between the two surfaces.

The full sphinx manual (`docs/sphinx/`) covers configuration,
watchdog and timeout policy, parallel-mode semantics, and the
maintained API surface. Use the
[Quickstart](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/quickstart.md)
once the first run is finished and you want the
[Developer reference](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/developer-reference.md)
or
[API reference (`modules.rst`)](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/modules.rst).

`ralph --init` also auto-symlinks the bundled skill bundle into the
supported agent roots and seeds a batteries-included `.gitignore`
covering Python, Node, Rust, Go, Ruby, PHP, Java/Kotlin, .NET,
Dart/Flutter, Elixir, Scala, Terraform, and common IDE/OS patterns.
Run those commands from a human-operated shell outside any Ralph-managed
agent session.

Before your first run: install the agent CLIs you want Ralph Workflow to
call, authenticate them normally, and pick one small concrete task.
`ralph --diagnose` is the optional **pre-flight check** — it verifies
your agent CLIs, MCP servers, and capability bundles are healthy before
you spend a real run on them. See the
[diagnostics page](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/diagnostics.md)
for what each check proves.

Depth presets:

```bash
ralph -Q     # quick: small fixes, single iteration
ralph        # standard: most features and tasks
ralph -T     # thorough: complex refactors, ten iterations
```

## Supported agents

Ralph Workflow ships with first-class support for six user-facing agent
CLIs: Claude Code, Codex, OpenCode, Nanocoder, Google Anti Gravity, and
Pi. Each agent has a documented end-to-end verification path in the
Sphinx manual; see the
[`agents`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/agents.md)
page for the canonical details.

| Agent | Description |
|---|---|
| **Claude Code** | Anthropic's CLI for Claude. The canonical reference agent. |
| **Codex** | OpenAI's Codex CLI. |
| **OpenCode** | Open-source terminal coding agent. |
| **Nanocoder** | Local-only TUI coding agent. |
| **Google Anti Gravity (AGY)** | Google's Antigravity CLI (`agy`, v1.0.9+). |
| **Pi** | Minimal coding agent. Headless mode is `pi --mode json <prompt>`. |

## What a run leaves you

A successful run leaves a single canonical artifact in `.agent/`:
a `development_result` artifact that names the change, the checks, and
the reviewer focus, so the morning-after review reads in under a minute
without reconstructing the run. The full reviewable output and a sample
receipt live in the maintained manual:

- [Example Review Bundle](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/example-review-bundle.md)

## Documentation

The maintained operator manual is on Codeberg:

- [Manual index](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/index.rst)
- [Configuration](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/configuration.md)
- [CLI reference](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/cli.md)
- [Developer reference](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/developer-reference.md)
- [API reference](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/modules.rst)

For contributors:

- [CONTRIBUTING.md](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/CONTRIBUTING.md)

## Trust and safety

Ralph Workflow runs the agents locally with your credentials. Read the
root [`README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md)
for the full coverage of: agent authentication, branch or worktree
expectations, unattended approval implications, cost, and human
validation responsibility. The watchdog surface (the **Idle watchdog**
section in the manual) bounds how long any operation may take; see
[Watchdogs and timeouts](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md).

## Development

Contributors should read the [package-side
CONTRIBUTING.md](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/CONTRIBUTING.md)
for the Python-specific dev build, the policy-driven pipeline model,
and the guardrails (60-second combined test budget, lint, typecheck,
resource-lifecycle, timeout, artifact-submission, fabrication). The
verification command is `make verify` from the package root.

For Pro / paid support plans, see the
[Pro Support](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/pro-support.md)
page.

## License

AGPL v3 — see [LICENSE](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/LICENSE).
