# Ralph Workflow

> Mirror of [codeberg.org/RalphWorkflow/Ralph-Workflow](https://codeberg.org/RalphWorkflow/Ralph-Workflow) — star/issues/discussion on Codeberg.

**Hand your coding agents a spec. Walk away. Come back to reviewable, tested commits.**

Ralph Workflow is a free, open-source composable loop framework that runs the coding agents you already use — Claude Code, Codex, or OpenCode — on your own machine. Simple at the center, powerful in composition.

[![PyPI](https://img.shields.io/pypi/v/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![PyPI downloads](https://img.shields.io/pypi/dm/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![Python](https://img.shields.io/pypi/pyversions/ralph-workflow.svg)](https://pypi.org/project/ralph-workflow/)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

*10,700+ lifetime PyPI downloads · 4,000+ in the last 30 days (pepy.tech, 2026-06-12).*

Run the coding agents you already use — Claude Code, Codex, OpenCode, Nanocoder, Google Anti Gravity, and Pi — on your own machine. Hand it a spec, walk away, come back to runnable, tested software.

## MCP server trust boundary

The standalone Ralph Workflow MCP server (`ralph-mcp`) binds to `127.0.0.1` and exposes the exec
surface only over loopback. When the optional `MCP_AUTH_TOKEN` environment variable is
set, requests must carry a matching `Authorization: Bearer <token>` header; the
comparison uses `hmac.compare_digest` to prevent timing-side-channel attacks. An unset
or empty `MCP_AUTH_TOKEN` is a no-op (the loopback bind is the trust boundary).

## Install and run

```bash
pipx install ralph-workflow   # 1. install
ralph --init                  # 2. scaffold .agent/ and PROMPT.md
$EDITOR PROMPT.md             # 3. edit PROMPT.md — your spec for the run
ralph                         # 4. run the unattended workflow
```

This also auto-symlinks the bundled skill bundle into the supported agent roots and seeds a batteries-included .gitignore covering Python, Node, Rust, Go, Ruby, PHP, Java/Kotlin, .NET, Dart/Flutter, Elixir, Scala, Terraform, and common IDE/OS patterns.

## Parallel execution model

Parallel plan execution is delegated to the executing AI agent. Plans declare `work_units` or `parallel_plan` to signal parallelization intent; the executing agent dispatches its own sub-agents to carry the work out. Ralph-managed fan-out is dormant in the bundled default and retained only for future use.

## What a run leaves you

Here is the actual finish-receipt from the bundled [empty-name-validation example](examples/first-review-bundle/) — a real, unedited handoff, not a mock-up. You read this when you come back instead of a transcript:

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

Want to follow a full first run? Read the [real-task walkthrough](https://ralphworkflow.com/blog/real-task-walkthrough-overnight-refactoring) or browse the [first-run guide](https://ralphworkflow.com/start)

> Ralph is free and runs locally — stars are the only signal we get that it's working for you, and they set what we build next. If a run shipped real software for you: ⭐ [star on Codeberg](https://codeberg.org/RalphWorkflow/Ralph-Workflow).

## What it does

Ralph Workflow takes the simple Ralph-loop idea — plan, build, verify — and turns it into a **composable loop framework** where each phase can loop independently and hand off to the next. A single `ralph` command spawns planning, development iteration, review, and fix cycles across multiple agents, then produces finished git commits you can review when you come back.

**This is not a chat window or a prompt tool.** It's an orchestrator — an operating system for autonomous coding — that runs real engineering pipelines unattended. Fire it off overnight, over lunch, or while you're in meetings. The default workflow ships strong enough to start with immediately; customize it later when you need more control.

The name comes from the original Ralph loop: repeat a strong prompt until the model can make real progress. Ralph Workflow takes that simple, powerful idea and adds planning before implementation, verification after development, agent fallbacks, agent-agnostic execution, and customizable pipelines so unattended runs keep moving and teams can review the results with confidence.

## Why it's different

| What most tools do | What Ralph Workflow does |
|---|---|
| One agent, one chat session | Multiple agents routed by phase (planning → dev → review → fix) |
| Copy-paste between tools | Agents hand off work through the repo, not context stuffing |
| Hit context limits halfway | Phase-based summaries + checkpoint files keep context tight |
| Locked to one vendor | Claude + Codex + OpenCode + Nanocoder + AGY + Pi in the same pipeline — your choice |
| "Look at the diff" | Runnable, tested software with integration checks |

[See how Ralph Workflow compares to 19 other autonomous coding tools →](https://ralphworkflow.com/compare)

## Who it's for

Developers and teams who have **ambitious, well-specified work** that's too big to babysit and too risky to trust blindly.

A good first run looks like:

1. **Write a spec** — what you want built, in plain English or markdown
2. **Run `ralph`** — the orchestrator plans, builds, tests, and iterates
3. **Review the commits** — come back to committed, tested code

**[Start here: your first unattended task →](https://ralphworkflow.com/start)**

New to autonomous coding? The 4-step guide walks you through picking a task, writing a short spec, running Ralph Workflow, and judging the result honestly — all in one page. Prefer a deeper narrative? [Read the blog version →](https://ralphworkflow.com/blog/your-first-overnight-task-start-here-guide)

Start with a bounded, verifiable task — the kind of work you would actually merge. A good first run is 2-6 hours, has a clear boundary, and a concrete correctness check. For a strong first run, pick a task with clear acceptance criteria: "add tests to an existing module so coverage reaches 80%", "refactor one subsystem with existing tests to confirm no regressions", or "build a fitness-app slice with concrete feature checks". The common thread is a well-specified outcome you can judge honestly when you return, not how small the task is.

## Install

### pipx (recommended)

```bash
pipx install ralph-workflow
ralph --help
```

### PyPI

```bash
pip install ralph-workflow
ralph --help
```

### Docker

```bash
docker run --rm -it -v "$(pwd):/workspace" -v "$HOME/.ralph:/root/.ralph" ralphworkflow/ralph --help
```

Build from source:

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
docker build -t ralph-workflow .
docker run --rm -it -v "$(pwd):/workspace" -v "$HOME/.ralph:/root/.ralph" ralph-workflow
```

### From source

```bash
git clone https://codeberg.org/RalphWorkflow/Ralph-Workflow.git
cd Ralph-Workflow/ralph-workflow
pip install -e .
ralph --version
```

Requires Python 3.12+.

**[Real-task walkthrough →](https://ralphworkflow.com/blog/real-task-walkthrough-overnight-refactoring)**

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

Depth presets control iteration intensity:

```bash
ralph -Q     # quick: small fixes, single iteration
ralph        # standard: most features and tasks
ralph -T     # thorough: complex refactors, ten iterations
```

## A fast way to tell whether Ralph Workflow fits

1. Pick one real backlog task that is small enough to review in one sitting.
2. Write it down in `PROMPT.md` with clear acceptance criteria.
3. Run Ralph Workflow.
4. Come back and ask one question: **would you merge this?**

If yes, give it a harder task next.
If no, tighten the spec, checks, or task choice and run again.

If the first run teaches you something real either way, turn that result into the right public Codeberg action: star/watch the primary repo if it earned trust, or report the exact first-run friction on Codeberg if it did not.

## What to expect from a run

Ralph Workflow is meant to get you to a strong implementation starting point while you are away, not to replace engineering judgment.

A good run should leave you with:

- code that compiles, tests, or clearly shows where work remains
- logs and output that explain what happened
- a result that is worth continuing from, not discarding and restarting

That may be a finished small task, or it may be a substantial first pass toward production on a larger one.

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

## Documentation

This README intentionally leaves out deeper implementation details and defers to the `docs/sphinx/` pages for those.

- **Quickstart:** [`docs/sphinx/quickstart.md`](docs/sphinx/quickstart.md) — shorter repeat-use reference with commands and flags
- **Getting Started:** [`docs/sphinx/getting-started.md`](docs/sphinx/getting-started.md) — fuller first-run walkthrough with task guidance
- **Concepts:** [`docs/sphinx/concepts.md`](docs/sphinx/concepts.md) — terminology and mental model
- **CLI Reference:** [`docs/sphinx/cli.md`](docs/sphinx/cli.md) — all flags and sub-commands
- **Configuration:** [`docs/sphinx/configuration.md`](docs/sphinx/configuration.md) — config files and precedence
- **Developer Reference:** [`docs/sphinx/developer-reference.md`](docs/sphinx/developer-reference.md) — maintained contributor and architecture reference
- **Modules Index:** [`docs/sphinx/modules.rst`](docs/sphinx/modules.rst) — API/module entry points for deeper internals
- **Adding and managing agent support:** [`docs/agents/README.md`](docs/agents/README.md) — entry point for adding, updating, or removing a built-in or custom agent

## Idle watchdog

The agent session watchdog judges whether a session is stuck. It used to
base that verdict entirely on stdout output, which is no longer a reliable
proxy: real work now happens through channels that don't emit stdout — Ralph Workflow
MCP tool calls, subagent delegation, and workspace file changes. A session
that was demonstrably working could be killed as idle; a session waiting on a
dead subagent could survive until a much larger ceiling.

The watchdog now considers **four evidence channels**:

- `stdout` — agent stdout output (the baseline)
- `mcp_tool` — Ralph Workflow MCP tool calls / completions
- `subagent` — delegated child progress / tool calls / heartbeats
- `workspace` — workspace file changes from `WorkspaceMonitor`

Workspace evidence collection runs whenever a run has a `workspace_path`,
regardless of whether the progress UI (`show_progress`) is enabled, so quiet
unattended runs that do real file work are not falsely killed.

While any non-stdout channel is fresher than the new
`agent_idle_activity_evidence_ttl_seconds` knob (under `[general]`, default
`30.0`), the `NO_OUTPUT_DEADLINE` fire is deferred and the watchdog returns
`CONTINUE`. Set the knob to `0.0` to opt out and restore the
legacy stdout-only behaviour.

"Activity" means **demonstrated work**, not mere existence: an OpenCode
subagent process that is alive but has produced no output, no tool calls,
and no file changes for the configured idle window is **not** evidence of
progress. Once scoped Ralph Workflow child evidence goes stale, the run falls back to
the normal idle timeout instead of lingering under the larger cumulative
waiting-on-child ceiling. Raw OS descendants alone defer the verdict only
when Ralph Workflow never had scoped visibility into the child in the first place.

Every HARD_STOP diagnostic and every deferred `CONTINUE` carries a
per-channel `evidence_summary` array with `{channel, last_at, age_seconds,
counter}` entries and an `active_channel` label, so a post-mortem reader
can see exactly which channels were fresh and which were stale at the moment
the verdict was reached.

The absolute `SESSION_CEILING_EXCEEDED` and `CHILDREN_PERSIST_TOO_LONG`
ceilings are checked BEFORE the deferral and remain absolute — no activity
can extend the maximum session duration or the cumulative waiting-on-child
ceiling.

For more details on watchdog configuration, per-reason backoff, and the forever-wait recovery state, see the [Timeout Policy documentation](docs/agents/timeout-policy.md).


## Privacy & Error Reporting

Ralph Workflow sends anonymous crash reports and performance metrics to help fix bugs and improve reliability. No personal data is collected.

Each installation generates a random 32-character identifier stored in `~/.config/ralph-workflow-user.ini`. This identifier is not tied to your name, email address, IP address, or any other personal data — it is a random string used only to distinguish different installations in crash reports. A fresh random session identifier is generated on every run.

To opt out: delete or rename `~/.config/ralph-workflow-user.ini`. Ralph Workflow creates a new random ID on the next run.

## Community

Already installed? Run **`ralph star`** from your terminal to open the primary repo, or visit <https://codeberg.org/RalphWorkflow/Ralph-Workflow>. Codeberg is primary — star, watch, fork, and open issues there first; GitHub is a read-only mirror.

Stars are the only signal we get that Ralph Workflow is working for you, and they set what we build next.

## Development and verification

If you are changing Ralph Workflow itself, start with [`CONTRIBUTING.md`](CONTRIBUTING.md) and run the canonical verification command before you finish:

```bash
make verify
```

## Pro support (optional GUI layer)

[Ralph-Workflow-Pro](https://codeberg.org/RalphWorkflow/Ralph-Workflow-Pro)
is an optional GUI layer that runs the engine as a subprocess. The
engine exposes a small, read-only, bounded surface so Pro can
monitor and (in advanced uses) inject custom pipeline
collaborators.

### Pipeline dependency injection

The engine's pipeline and plumbing commands share the same underlying
execution core through a single injectable dependency bundle,
`PipelineDeps` (`ralph.pipeline.factory`). The bundle carries the four
primary collaborators:

- **display** — `display_context` drives all output surfaces. Plumbing
  commands resolve it from the injected `PipelineDeps` when it is not
  supplied as a separate argument, matching the main run loop's
  `PipelineDeps`-first contract.
- **model** — `model_identity` is forwarded through the session bridge to
  `AgentSession`.
- **prompt** — `system_prompt_materializer` is consumed inside
  `execute_agent_effect` and is shared by both the main pipeline and
  plumbing. `phase_prompt_materializer` is used by the main pipeline for
  phase handoff prompts; plumbing commands build single-task prompts
  directly and do not route them through the phase materializer.
- **artifact requirements** — `artifact_requirements_resolver` resolves the
  required artifact contract for each phase/drain. The commit plumbing
  path preserves an injected resolver and only falls back to its
  commit-specific resolver when the bundle still contains the default
  production implementation.

The main pipeline (`ralph.pipeline.runner`) and plumbing commands
(`--generate-commit`, smoke test) both build a `PipelineDeps` via
`build_default_pipeline_deps` and execute agents through
`execute_agent_effect`. `display_context`, `model_identity`,
`system_prompt_materializer`, and `artifact_requirements_resolver` are
consumed inside `execute_agent_effect`; the main pipeline additionally
routes `phase_prompt_materializer` through
`materialize_agent_prompt_if_needed` before each agent invocation so
phase prompts are materialized by the same injected collaborator.

Pro can inject custom implementations of any of these collaborators
through `ProPipelineHooks`, and `build_default_pipeline_deps` applies
those overrides to the returned `PipelineDeps` without changing the
shared execution core. Existing tests exercise this contract:

- `tests/test_pipeline_factory.py` proves the four collaborators live
  in `PipelineDeps`, that `execute_agent_effect` consumes
  `artifact_requirements_resolver`, and that `ProPipelineHooks` can
  override each collaborator.
- `tests/integration/test_plumbing_shared_deps.py` proves plumbing
  commands receive and forward the same `PipelineDeps` bundle to the
  shared execution core, that `display_context` is resolved from the
  bundle when omitted, and that an injected
  `artifact_requirements_resolver` is preserved by the commit path.
- `tests/test_run_loop_pro_integration.py` proves `PipelineDeps`
  composed with `ProPipelineHooks` reaches the inner pipeline loop.

- Engine-side contract page: [`docs/sphinx/pro-support.md`](docs/sphinx/pro-support.md).
- Engine-side engine-capability traceability: [`docs/agents/pro-contract.md`](docs/agents/pro-contract.md).
- Upstream contract (authoritative source of truth): `Ralph-Workflow-Pro/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md`.
