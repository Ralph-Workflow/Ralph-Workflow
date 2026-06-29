# Ralph Workflow — the autopilot for coding agents

> **Codeberg is primary.** Star, watch, fork, and report issues there first:
> <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
> GitHub is a read-only mirror:
> <https://github.com/Ralph-Workflow/Ralph-Workflow>

Ralph Workflow is **the autopilot for coding agents** — a free and
open-source **AI agent orchestrator** that runs the coding agents you already
use, on your own machine. Hand it a well-specified task, let the agents plan,
build, verify, and fix, and come back to reviewable, tested work.

It's an **operating system for autonomous coding**: a composable loop
framework built around a simple Ralph-loop core that becomes powerful through
composition. The default workflow is strong enough to adopt as-is, before you
customize anything.

[Install](#install) · [Start your first run](#start-your-first-run) · [Read the docs](https://ralphworkflow.com/docs) · [Source on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow) · [Submit an issue](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new)

## What you get back from a run

This is the actual finish-receipt from a real bundled example — a real,
unedited handoff you read in the morning instead of a transcript:

```text
# Development Result

## Outcome
Implemented empty-name validation in the CLI create flow and added
test coverage for empty and whitespace-only input.

## Changed files
- cli/create.py
- tests/test_create.py

## Checks run
- pytest tests/test_create.py        ✓ passed
- project formatting / lint checks    ✓ passed

## Reviewer focus
- confirm validation happens before any file creation side effect
- confirm the error message is clear enough for CLI users
- confirm no unrelated flow changed
```

Sample unedited terminal captures from a real run (Ralph Workflow v0.8.8): [`ralph --init`](assets/demo/init-output.txt) · [`ralph --diagnose`](assets/demo/diagnose-output.txt) · [`ralph --dry-run`](assets/demo/dry-run-output.txt).

## What it is

Ralph Workflow extends the simple Ralph loop — plan, build, verify — into a
**composable loop framework**. Each phase can loop independently, recover on
failure, and hand off to the next phase. One `ralph` command runs planning,
development, review, fix, and recovery cycles across multiple agents, then
hands back finished git commits and a development_result receipt.

This is **not** a chat window, a prompt tool, or a single-agent loop. It's an
orchestrator for unattended engineering work.

## Who it's for

If one of these describes you, Ralph Workflow is built for you:

- **The solo builder.** You have side projects with real spec depth — you
  know what to build, but you're one person. Set `PROMPT.md` before bed, wake
  up to reviewed commits.
- **The team lead.** Ralph Workflow fits between PR and review — unattended
  verification that your agents shipped what you asked for, not what they
  guessed.
- **The AI tool builder.** You're already wiring Claude Code into your
  workflow. Ralph Workflow gives you the loop pattern — phase routing, cost
  arbitrage, recovery, checkpointing — as infrastructure instead of
  something you'd build yourself.

**Ralph Workflow is not for** one-line fixes, vague prompts, or repos
without tests. It's for **ambitious, well-specified work** you'd trust a
capable colleague to do unattended. A repo without guardrails will produce
results that reflect that.

## Why it's different

| What most tools do               | What Ralph Workflow does                                              |
| -------------------------------- | --------------------------------------------------------------------- |
| One agent, one chat session      | Multiple agents routed by phase (plan → dev → review → fix → recover) |
| Copy-paste between tools         | Agents hand off work through the repo, not context stuffing           |
| Hit context limits halfway       | Phase-based summaries + checkpoint files keep context tight           |
| Locked to one vendor             | Claude + Codex + OpenCode + Nanocoder + AGY + Pi in one pipeline      |
| "Look at the diff"               | Runnable, tested software with integration checks                     |
| Single-agent idle watchdog       | Four-channel evidence: stdout + MCP + subagent + workspace            |

## Start your first run

```bash
pipx install ralph-workflow        # 1. install
cd /path/to/your/project           # 2. pick a real repo
ralph --init                       # 3. scaffold .agent/ + PROMPT.md
ralph --diagnose                   # 4. pre-flight: verify agents, MCP, capabilities
$EDITOR PROMPT.md                  # 5. write the task — see PROMPT.md template
ralph                              # 6. run the unattended workflow
```

Run those commands from a human-operated shell outside any Ralph-managed
agent session.

- `ralph --init` provisions the default local work surface and shipped
  baseline skills.
- `ralph --diagnose` is the **pre-flight check**: it verifies your agent
  CLIs, MCP servers, and capability bundles are healthy. See the
  [diagnostics page](ralph-workflow/docs/sphinx/diagnostics.md) for what each
  check proves.

When you come back, ask one question: **would I merge this?** If yes, give it
a harder task next. If no, tighten the spec, the checks, or the task choice
and run again.

The shortest path is [START_HERE.md](START_HERE.md). The full walkthrough is
in the [maintained manual](ralph-workflow/docs/sphinx/index.rst).

## Trust and safety boundaries

These are not negotiable. They are how the tool is designed:

- **Local execution.** Ralph Workflow runs on your machine. It does not
  upload your code or data to a cloud service. Crash reports are anonymous
  and opt-out-able — see *Privacy* in the package README.
- **Agent authentication is yours, not Ralph's.** Ralph Workflow does not
  store, read, or proxy agent credentials. Each agent CLI uses its own
  native authentication (vendor login or API key). You authenticate each
  agent first, and Ralph Workflow then invokes those CLIs as-is.
  See [the Agent CLI lifecycle page](ralph-workflow/docs/sphinx/agents.md)
  for the full selection, detection, and invocation story.
- **Branch / worktree expectations.** A long run writes files and may
  create branches. Run on a clean worktree and review with your normal git
  workflow before merge.
- **Unattended approval implications.** "Unattended" means agents may keep
  writing while you sleep. Have backups and branch protection. See
  [`bounded-autonomy-for-unattended-coding.md`](ralph-workflow/docs/sphinx/bounded-autonomy-for-unattended-coding.md)
  for the safety model.
- **Cost.** Agent calls are on your cloud bill. Ralph Workflow itself has
  no per-run fee.
- **Human responsibility.** Agents handle the long middle. You handle the
  judgment: run the code against the real environment, check the diff, look
  at the receipts, decide.

## Supported agents

Ralph Workflow ships with first-class support for six user-facing agent
CLIs: Claude Code (interactive + headless), Codex, OpenCode, Nanocoder,
Google Anti Gravity (AGY), and Pi. Each has a documented end-to-end
verification path. See
[Agent Compatibility](ralph-workflow/docs/sphinx/agent-compatibility.md) for
the full matrix and known caveats.

## Runtime, license, project home

- **Runtime:** Python ≥ 3.12. Local-first; no required cloud account.
- **License:** AGPL-3.0-or-later.
- **Project home (primary):** <https://codeberg.org/RalphWorkflow/Ralph-Workflow>
- **PyPI:** <https://pypi.org/project/ralph-workflow/>
- **Documentation site:** <https://ralphworkflow.com/docs>
- **Issue tracker:** <https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new>
- **Contribution route:** [CONTRIBUTING.md](CONTRIBUTING.md) →
  [ralph-workflow/CONTRIBUTING.md](ralph-workflow/CONTRIBUTING.md)

## Documentation route

The shortest read is the README → START_HERE → docs-map → manual route:

1. **README.md** (this file) — what it is, who it's for, fastest install
2. **[START_HERE.md](START_HERE.md)** — guided first run
3. **[docs/README.md](docs/README.md)** — route by intent (evaluate / install
   / configure / contribute / understand architecture)
4. **[ralph-workflow/docs/sphinx/index.rst](ralph-workflow/docs/sphinx/index.rst)**
   — the maintained operator manual

For contributor and architecture detail, the
[`docs/architecture/`](docs/architecture/README.md) overview explains the
Python runtime end-to-end.

## Ecosystem and attribution

The Ralph Loop pattern is attributed to
[Geoffrey Huntley (ghuntley.com/ralph)](https://ghuntley.com/ralph).
Ralph Workflow is an independent reference implementation — not the
pattern's originator. See [ECOSYSTEM.md](ECOSYSTEM.md) for the broader
ecosystem of pattern derivatives and live integrations.

## Call to action

Pick **one**. They're all signals that shape what we build next.

- ⭐ [Star the primary repo](https://codeberg.org/RalphWorkflow/Ralph-Workflow)
- ▶ [Run your first real task](START_HERE.md)
- 📖 [Read the operator manual](ralph-workflow/docs/sphinx/index.rst)
- 🐛 [Report a first-run friction](https://codeberg.org/RalphWorkflow/Ralph-Workflow/issues/new)
- 🤝 [Contribute](CONTRIBUTING.md)