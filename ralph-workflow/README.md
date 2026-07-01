<!--
  Review note (docs rubric §"Required review note for meaningful docs changes"):
  - What changed: this PyPI README now reads as a storefront, not a
    manual. The AGY section was collapsed to a one-sentence pointer
    (the smoke command with env var, parity-table excerpt, live
    regression test names/counts, and `tmp/agy-source-of-truth.txt`
    reference were all removed — they live in the Sphinx manual).
    The standalone "Idle watchdog" H2 was folded into Trust and
    safety as a one-line pointer. The "Install and run" and "Quick
    start" sections were collapsed into a single install-and-run
    block. The Documentation list was trimmed to four operator
    entries with contributor material under a sub-bullet. The
    rolling 30-day PyPI download clause was pruned (the date was
    2026-06-12 and refreshing it would require network-dependent
    level-2 verification); only the evergreen lifetime figure is
    retained. The Trust and safety block was widened with one-line
    pointers to the root README's full coverage (agent
    authentication, branch/worktree expectations, unattended
    approval implications, cost, human validation responsibility).
  - Why it belongs here: this file is the PyPI-facing README
    (`[project] readme = "README.md"` in pyproject.toml). PyPI
    readers want to know what the package is, whether it fits, and
    how to install it; deeper operator detail belongs in the
    Sphinx manual.
  - What was pruned, merged, or explicitly left alone: the AGY
    smoke command + parity table + live-regression test counts,
    the `tmp/agy-source-of-truth.txt` reference, the standalone
    "Idle watchdog" H2, the "Quick start" duplicate, the rolling
    30-day download clause, and several duplicate Documentation
    bullets were pruned. The supported-agents table is preserved
    because it is a single-page summary that PyPI readers want.
    The verbatim finish-receipt block is preserved here (PyPI
    readers may not click through to the manual).
  - How duplication was reduced or contained: root README is the
    single source for the full 6-step first-run block; this README
    repeats the trimmed 4-step version (install → init → write spec
    → run) for PyPI context and defers all deeper material via
    Sphinx links. The finish-receipt is preserved on PyPI but
    lives once on the root README in the maintained docs surfaces.
  - How the route is clearer now than before: what-it-is → who-it's-for →
    install-and-run → supported-agents → what-a-run-leaves-you →
    documentation → fit-or-not-fit → trust-and-safety → development →
    pro-support-pointer. Manual-depth technical detail is now reached
    only via Sphinx links, never duplicated on this page.
-->

# Ralph Workflow — the autopilot for coding agents

> **Codeberg is primary.** Star, watch, fork, and report issues there first:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow> (verify: repo-exists)
> GitHub is a read-only mirror:
> <https://github.com/Ralph-Workflow/Ralph-Workflow> (verify: repo-exists)

Ralph Workflow is **the autopilot for coding agents** — a free and
open-source operating system for autonomous coding, an AI agent
orchestrator built around a simple Ralph-loop core that becomes powerful
through composition.

**Hand it a well-specified coding task, let the agents plan, build,
verify, and fix, and come back to reviewable, tested work.**

The default workflow is strong enough to adopt as-is, before you
customize anything.

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![PyPI downloads](https://img.shields.io/pypi/dm/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

*Live lifetime PyPI downloads: [pepy.tech/projects/ralph-workflow](https://pepy.tech/projects/ralph-workflow) (audited 2026-06-30; no hardcoded number — refresh the page for the current count).*

## What it is

Ralph Workflow is an **operating system for autonomous coding**: the
agents handle the long middle of engineering work while you handle the
judgment that only a human can make. **Hand it a well-specified coding
task, let the agents plan, build, verify, and fix, and come back to
reviewable, tested work.**

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
colleague to do unattended. It runs the agents you already use — Claude
Code, Codex, OpenCode, Nanocoder, Google Anti Gravity, and Pi — on your
own machine, with your keys to yourself.

It is **not** for one-line fixes, vague prompts, or repos without tests.
A repo without guardrails will produce results that reflect that.

## Install and run

The fastest PyPI install:

```bash
pipx install ralph-workflow
```

The full first-run path (install → init → write `PROMPT.md` → run → review) is the single source of truth in the root [`README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md) — see the **"Start your first run"** section. PyPI readers should follow the root README for the 6-step walkthrough; this README intentionally leaves out the deeper operator material to avoid drift between the two surfaces.

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

The finish-receipt handoff is documented as the single source of truth in the root [`README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md) **"What a run leaves you"** section — the verbatim receipt, the bundled-example reference, and the sample unedited terminal captures all live on the root README so PyPI readers and forge readers see the same proof.

Want to follow a full first run? Read the
[real-task walkthrough](https://ralphworkflow.com/blog/real-task-walkthrough-overnight-refactoring)
or browse the [first-run guide](https://ralphworkflow.com/start).

## Documentation

This README intentionally leaves out deeper implementation details and
defers to the Sphinx operator manual for those.

- **Quickstart:** [`docs/sphinx/quickstart.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/quickstart.md) —
  shorter repeat-use reference with commands and flags
- **Getting Started:** [`docs/sphinx/getting-started.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/getting-started.md) —
  fuller first-run walkthrough with task guidance
- **Configuration:** [`docs/sphinx/configuration.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/configuration.md) —
  config files and precedence
- **CLI Reference:** [`docs/sphinx/cli.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/cli.md) —
  all flags and sub-commands

Contributor material:

- **Modules Index:** [`docs/sphinx/modules.rst`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/modules.rst)
- **Developer Reference:** [`docs/sphinx/developer-reference.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/developer-reference.md)
- **Adding and managing agent support:** [`docs/agents/README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/agents/README.md)

## When Ralph Workflow fits (and when it doesn't)

**Fits:**

- Multi-step tasks that outgrow one prompt
- Work you want to review after the fact instead of steering live
- Teams that want AI execution to stay in the repo
- Runs where you want to mix stronger and cheaper models by phase

**Does not fit:**

- One-shot interactive prompts
- Pair-programming sessions with constant human steering
- Tiny tasks where setup overhead is not worth it
- Workflows that need unpredictable mid-run human input

## Privacy & Error Reporting

Ralph Workflow sends **anonymous, metalevel** crash reports and performance
metrics to help fix bugs and improve reliability. No personal data is
collected, and nothing about the project you are working on ever leaves
your machine.

Each installation generates a random 32-character identifier stored in
`$XDG_CONFIG_HOME/ralph-workflow-user.ini` when `XDG_CONFIG_HOME` is
set, falling back to `~/.config/ralph-workflow-user.ini` otherwise.
This identifier is not tied to your name, email address, IP address,
or any other personal data — it is a random string used only to
distinguish different installations in crash reports. A fresh random
session identifier is generated on every run.

What we collect (anonymous metadata only):

- **Operating system, architecture, and environment markers** (CI,
  container, WSL, Codespaces, SSH session, package manager).
- **Python and Ralph Workflow versions** (e.g. Python `3.12.5`, Ralph Workflow `0.8.18`).
- **Whether you are running inside a virtualenv** (boolean only — the
  virtualenv path is never sent).
- **Session timing** (start, duration) and a **coarse exit outcome**
  (`success` / `failure` / `interrupted` / `unknown` for utility
  invocations).

What we never collect:

- Your **prompts**, **inline arguments**, or any other user input.
- The **current working directory**, **argv**, **config path**, or any
  other filesystem location tied to your codebase.
- **Stack-frame absolute paths** — these are stripped to basenames
  before being sent, so the codebase you are working on is never
  identified.
- **Hostnames**, **usernames**, **environment-variable values**, or any
  other personally identifying detail.

How to opt out (any one of these disables telemetry entirely):

- Set the environment variable `RALPH_DISABLE_TELEMETRY=1` (any of
  `1`, `true`, `yes`, `on`, case-insensitive).
- Delete or rename your identity file. The path follows the
  `XDG_CONFIG_HOME` convention: when `XDG_CONFIG_HOME` is set, the
  file lives at `$XDG_CONFIG_HOME/ralph-workflow-user.ini`; otherwise
  it falls back to `~/.config/ralph-workflow-user.ini`. On the next
  run, Ralph Workflow will create a new random ID only if telemetry
  is enabled.

## Community

Already installed? Run **`ralph star`** from your terminal to open the
primary repo, or visit
<https://codeberg.org/RalphWorkflow/Ralph-Workflow>. Codeberg is primary
— star, watch, fork, and open issues there first; GitHub is a read-only
mirror. Stars are the only signal we get that Ralph Workflow is working
for you, and they set what we build next.

## Trust and safety

Ralph Workflow runs locally on your own machine and does not upload
your code or data to a cloud service. For the full coverage of trust
and safety boundaries (agent authentication, branch/worktree
expectations, unattended approval implications, cost, and human
validation responsibility), see the root
[`README.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/README.md)
"Trust and safety" section.

## Development and verification

If you are changing Ralph Workflow itself, start with
[`CONTRIBUTING.md`](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/CONTRIBUTING.md)
and run the canonical verification command before you finish:

```bash
make verify
```

## Pro support (optional GUI layer)

[Ralph-Workflow-Pro](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/pro-support.md)
is an optional GUI layer that runs the engine as a subprocess. For the
engine-side contract and the default-factory wiring, see the
[Pro support](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/ralph-workflow/docs/sphinx/pro-support.md)
page in the operator manual.