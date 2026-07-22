# Configuration Reference

> **New to Ralph Workflow?** Start with [Getting Started](getting-started.md) before diving into config details.

Use this page when your question is about files, precedence, validation commands, or configuration edits.

If your immediate question is **"Where do I edit `ralph-workflow.toml`?"**, the short answer is:

- **Global defaults for all projects** → `~/.config/ralph-workflow.toml`
- **Project-specific override for just this repo** → `.agent/ralph-workflow.toml`

If `.agent/ralph-workflow.toml` does not exist yet, create it with:

```bash
ralph --init-local-config
```

After editing config, validate it with:

```bash
ralph --check-config
ralph --check-policy
```

Ralph Workflow uses layered configuration. Settings are resolved in this order, highest priority first:

1. **CLI flags**
2. **Project-local config** — `.agent/ralph-workflow.toml`
3. **User-global config** — `~/.config/ralph-workflow.toml`
4. **Bundled defaults** — shipped in `ralph/policy/defaults/`

## The files most operators care about

Ralph Workflow manages a standard config set across two scopes.

### User-global files

| File | Purpose |
|------|---------|
| `~/.config/ralph-workflow.toml` | Global defaults: agent selection, iteration counts, verbosity |
| `~/.config/ralph-workflow-mcp.toml` | MCP server definitions shared across projects |
| `~/.config/ralph-workflow-pipeline.toml` | Global pipeline defaults when a workspace has no local pipeline override |
| `~/.config/ralph-workflow-artifacts.toml` | Global artifact defaults when a workspace has no local artifact override |

### Project-local files

| File | Purpose |
|------|---------|
| `.agent/mcp.toml` | Project-specific MCP server definitions |
| `.agent/pipeline.toml` | Workflow phases, routing, and parallel settings |
| `.agent/artifacts.toml` | Artifact type schemas and contracts |
| `.agent/ralph-workflow.toml` | Optional project-specific overrides for agents, chains, drains, and main settings |

Run `ralph --init` to create the standard project-local support files. Use `ralph --init-local-config` when you explicitly want a project-local copy of the main config.

## Advanced config map

If you already know you want the deeper docs, use this map instead of scanning the whole manual:

| I want to change... | Open this page |
|---|---|
| agent selection, retry behavior, verbosity, or drain bindings | this page: [Configuration Reference](configuration.md) |
| workflow phases, loopbacks, commit routes, fan-out, counters, or recovery | [Advanced Pipeline Configuration](advanced-pipeline-configuration.md) |
| artifact contracts, decision vocabularies, summary files, or commit-message artifacts | [Advanced Artifact Configuration](advanced-artifact-configuration.md) |
| MCP servers, web search, crawl, or media/web-visit integrations | [Advanced MCP Configuration](advanced-mcp-configuration.md) |
| what the active policy means after all config layers resolve | [Policy Explanation](configuration.md#inspecting-the-active-policy) |

## Which file should I edit?

Use this rule of thumb:

- **I want this behavior in every repo I run** → edit `~/.config/ralph-workflow.toml`
- **I only want this behavior in one repo** → edit `.agent/ralph-workflow.toml` **unless the change is about workflow shape, phases, or loopbacks**
- **I want to change workflow phases, loopbacks, counters, or phase-owned policy** → edit `.agent/pipeline.toml`, then read [Advanced Pipeline Configuration](advanced-pipeline-configuration.md)
- **I want to change MCP servers or web/search access** → edit `~/.config/ralph-workflow-mcp.toml` or `.agent/mcp.toml`
- **I want to change artifact contracts/history** → edit `.agent/artifacts.toml`

The common mistake is editing `ralph-workflow.toml` when the real change belongs in `pipeline.toml`. The main `ralph-workflow.toml` file is mostly for:

- agent selection and fallback chains
- drain-to-chain bindings
- retry / timeout / verbosity settings
- Claude Code Switch / agent definitions

The workflow structure itself lives in `pipeline.toml`.

## The fastest safe workflow for editing config

1. Decide whether the change is **global** or **repo-local**.
2. Edit the right TOML file.
3. Run `ralph --check-config`.
4. If you changed workflow behavior, also run `ralph --check-policy`.
5. Run `ralph --diagnose` before the next real unattended run.

If you want the active workflow explained in plain English after the config change, run:

```bash
ralph --explain-policy
```

## Bundled defaults

The bundled defaults live in `ralph/policy/defaults/`. When in doubt, the files themselves are the most exact reference:

- `ralph-workflow.toml` — main config
- `mcp.toml` — MCP server config
- `pipeline.toml` — workflow phases and routing
- `artifacts.toml` — artifact contracts

## Environment variables

Ralph Workflow honours a small set of environment variables. These are inputs to the engine, not extensions of the 60-second combined test budget.

### Pro integration contract

The Pro↔Ralph Workflow contract uses exactly three engine-facing variables (see `ralph/pro_support/env.py`):

| Variable | Purpose |
|----------|---------|
| `PROMPT_PATH` | Absolute or relative path to the operator-authored run spec. When set, Ralph Workflow prefers this over `<workspace>/PROMPT.md`. This is the canonical way to point the engine at a non-default prompt file. |
| `RALPH_WORKFLOW_PRO` | Non-empty truthy marker that the engine is running as a Ralph Workflow Pro subprocess. |
| `RALPH_WORKSPACE` | Absolute or relative path to the workspace root. When set, Ralph Workflow prefers this over the current working directory when resolving the workspace scope. |

### Operator/runtime variables

| Variable | Purpose |
|----------|---------|
| `RALPH_AGY_BINARY` | Path to a custom `agy` executable, or to the deterministic mock at `tests/_support/mock_agy.sh` for CI. See [CLI Reference](cli.md) and [Agent Compatibility](agent-compatibility.md). |
| `RALPH_CURSOR_BINARY` | Path to a custom `agent` executable (Cursor Agent CLI). The override points at a real wrapper, alternate live binary, or an operator-wired test stub. There is no bundled mock for Cursor (unlike AGY). See [CLI Reference](cli.md) and [Agent Compatibility](agent-compatibility.md). |
| `RALPH_INLINE_SKILLS_DIR` | Directory whose skill files are inlined into prompts through `SKILLS_INLINE_CONTENT` instead of relying on the shipped skill bundle. See [Getting Started](getting-started.md). |
| `XDG_CONFIG_HOME` | When set, Ralph Workflow places the user-global config at `$XDG_CONFIG_HOME/ralph-workflow.toml` instead of `~/.config/ralph-workflow.toml`. |

### Test-only timeout variables

| Variable | Purpose |
|----------|---------|
| `RALPH_PYTEST_TEST_TIMEOUT_SECONDS` | Per-test timeout passed by the `ralph.verify_timeout` wrapper. |
| `RALPH_PYTEST_SUITE_TIMEOUT_SECONDS` | Per-suite invocation timeout passed by the `ralph.verify_timeout` wrapper. |

These timeout variables are set by the test harness; they do **not** extend the 60-second combined budget enforced by `make verify`. See [Verification Model](concepts.md#verification-model) for the non-circumvention rule.

## Common settings in `ralph-workflow.toml`

The main config file is `~/.config/ralph-workflow.toml`, with optional project-level overrides in `.agent/ralph-workflow.toml`.

Telemetry identity is stored separately at `~/.config/ralph-workflow-user.ini`.
That path is intentionally independent of `XDG_CONFIG_HOME`, so terminal
applications that use different XDG environments still share one persistent
random identifier. If an older `$XDG_CONFIG_HOME/ralph-workflow-user.ini`
exists, Ralph Workflow migrates its valid identifier to the canonical path on
first use. The file contains no name, email, host, repository, or prompt data.

### `[general]`

Core workflow settings: verbosity, git identity, retry behavior, and liveness limits. See `ralph/policy/defaults/ralph-workflow.toml` for the canonical defaults and inline `# comment` lines documenting the semantics of each key.

| Key | Default | Description |
|-----|---------|-------------|
| `verbosity` | `2` | Output verbosity: 0=quiet, 1=normal, 2=verbose, 3=full, 4=debug |
| `telemetry_enabled` | `true` | Anonymous metadata-only telemetry is enabled by default. Set to `false` to opt out from user-global or project-local `ralph-workflow.toml`. |
| `git_user_name` | (from git config) | Git author name for commits |
| `git_user_email` | (from git config) | Git author email for commits |
| `auto_integrate_enabled` | `true` | On by default: at each of the four integration seams (see [Auto-integration triggers and skips](#auto-integration-triggers-and-skips)) Ralph Workflow rebases the current feature branch onto the shared mainline (falling back to a single endpoint merge on conflict) and fast-forwards the local mainline ref to the feature tip. Never pushes to a remote and never force-moves the mainline. A no-op on single-branch workflows via the skip conditions (on the target branch, no commits beyond the target, detached HEAD, missing target). Set to `false` to keep git behaviour byte-identical to runs without auto-integration. |
| `auto_integrate_target` | (auto-detect) | Shared integration branch name. When set (e.g. `"develop"`) it is used verbatim, provided that branch exists locally or can be materialized from `refs/remotes/origin/<target>`. When unset, the target is auto-detected: the remote default branch (`origin/HEAD`) when a remote exists, otherwise `main`, otherwise `master`. If no candidate exists the step skips with a recorded reason and never guesses. |
| `auto_integrate_fetch_enabled` | `true` | On by default: before each integration attempt Ralph Workflow runs a bounded, read-only `git fetch origin <target>` and fast-forwards the local mainline ref when the remote-tracking ref is strictly ahead. Never force-moves a ref and never pushes; a diverged remote is left alone. Set to `false` to keep the step strictly local -- appropriate when every agent shares one git common directory through linked worktrees, where the mainline ref is already shared. |
| `auto_integrate_fetch_timeout_seconds` | `10.0` | Wall-clock budget for the auto-integration fetch (must be `> 0` and `<= 120`). On timeout or any remote failure the step falls back to local-only integration and the run is never failed by an unreachable remote. The degradation is not silent: the refresh outcome (`origin unreachable`) is recorded on the run state and rendered to the operator in the `auto-integrate:` line. |
| `auto_integrate_resolve_timeout_seconds` | `900.0` | Wall-clock ceiling for ONE conflict-resolution agent invocation during auto-integration (must be `> 0` and `<= 7200`). On expiry the invocation is cut, the in-progress merge is aborted and the integration records a conflict, so a hung resolver can never stall the run with a merge in progress. At most two CONSECUTIVE unresolved conflicts against the same target may invoke the resolver; after that the integration records an escalation naming the blocked target and stops invoking an agent until a later integration lands. |
| `max_retries` | `3` | Max retries per agent attempt when synthesized from the main config |
| `retry_delay_ms` | `1000` | Base delay between retries |
| `backoff_multiplier` | `2.0` | Exponential backoff multiplier |
| `max_backoff_ms` | `60000` | Maximum retry backoff delay |
| `max_cycles` | `3` | Maximum full fallback cycles through a drain |
| `agent_idle_timeout_seconds` | `300.0` | Max idle seconds before a stalled agent is terminated |
| `agent_idle_activity_evidence_ttl_seconds` | `30.0` | Per-channel activity TTL: while any non-stdout channel is fresher than this, the `NO_OUTPUT_DEADLINE` fire is deferred and the watchdog returns `CONTINUE`. Set to `0.0` to opt out and restore the legacy stdout-only behaviour. |
| `agent_workspace_change_weights` | `{ source = 1.0 }` | Per-kind workspace file-change weights used by the activity-aware watchdog. Operators who previously relied on log-file activity can opt in with `agent_workspace_change_weights = { source = 1.0, log = 1.0 }`. See [Watchdogs and Timeouts](concepts.md#watchdogs). |

### Auto-integration triggers and skips

Auto-integration does **not** run only after a commit. With
`auto_integrate_enabled = true` it runs at four seams:

1. **The commit seam.** After a commit phase that actually created a
   commit (`COMMIT_SUCCESS`). This is the full sequence: durable crash
   record, rebase, endpoint-merge fallback, optional agent conflict
   resolution, fast-forward.
2. **Every successful phase boundary.** Eleven phase-transition events
   (agent success, analysis success, analysis/phase loopback, phase
   advance, review clean, review issues found, and their siblings) run
   the same integration so the feature branch stays in lockstep with a
   mainline that moved while an analysis phase ran.
3. **The parallel fan-out join.** After parallel work units are joined
   back together.
4. **Run startup.** Once per run, before the first phase, so a run that
   resumes onto a mainline that moved while it was stopped integrates
   before doing anything else.

An integration attempt can be skipped, and **how visible a skip is
depends on the seam** -- phase boundaries fire far more often than
commits, and a routine nothing-to-do there is not a fault:

* **The commit seam** records every skip it can produce on the run
  state and surfaces it in the `auto-integrate:` log line. The dirty
  worktree check is not one of them: it exists only on the boundary
  path below.
* **Phase boundaries and the fan-out join** run a cheap pre-check
  first, and that pre-check returns *without recording anything* when
  the workspace root is not a git checkout, when no integration target
  can be resolved, when the worktree has uncommitted **tracked**
  changes *and* the resolved target is already contained in `HEAD`
  (logged at INFO), when the target already sits at the feature tip
  **and** the origin refresh that pointer was read through was
  healthy, or when the pre-check itself raised (logged at WARNING).
  A dirty pre-check whose target *does* carry commits the checkout
  lacks is the exception: that deferral suppressed a genuine
  cross-agent catch-up, so it is **recorded** as a
  `worktree not clean` skip rather than staying invisible. Anything it
  does not short-circuit falls through to the same recorded path as
  the commit seam. The other case that deliberately breaks the silence
  is an
  already-integrated tip read through an *unhealthy* refresh --
  `origin unreachable`, `diverged from origin`, `no local branch`, or
  `lost a concurrent refresh race` -- which is recorded as a
  `no commits beyond target` skip carrying that refresh outcome,
  because a no-op computed from an unverifiable pointer is
  indistinguishable from a healthy one.
* **Run startup** uses the same pre-check but is never invisible: when
  nothing is recorded it still prints one
  `auto-integrate: startup check: nothing to integrate` line, so an
  operator can tell the sync ran at all.

The skip reasons seen most often are below. Reasons that wrap an
underlying git error (`preconditions not met`, `HEAD read failed`)
carry that error's text verbatim in the recorded reason, and rarer
failure-path reasons such as `unexpected failure: ...` are recorded the
same way.

| Skip | Meaning |
|------|---------|
| worktree not clean | **Phase boundaries, fan-out join and startup only.** The probe runs `git status --porcelain --untracked-files=no`, so only uncommitted **tracked** modifications defer a boundary integration -- the same definition of "clean" the commit seam's rebase preconditions already use. Untracked scratch files no longer suppress cross-agent synchronisation: the phase boundary is the only seam that carries another agent's landing to an agent that is not committing right now, so an agent holding a stray scratch file would otherwise never receive a sibling's work. Untracked work in flight stays safe because `git rebase`/`git merge` refuse non-destructively, and only for the specific untracked path they would overwrite, which routes into the endpoint-merge fallback. Any git failure here also counts as "not clean" (fail closed). The deferral is **recorded on run state** (and surfaced in the `auto-integrate:` line) when the resolved target carried commits the checkout lacked -- a genuinely suppressed catch-up -- and otherwise remains an INFO log line only (`phase-transition integration deferred; worktree dirty`), plus at the startup seam the generic `startup check: nothing to integrate` line. |
| on target branch | The checkout is already on the mainline; there is nothing to integrate. |
| no commits beyond target | The target ref already equals `HEAD`. |
| detached HEAD | There is no branch to integrate. |
| no integration target branch resolved | Neither the configured target nor any auto-detect candidate exists. |
| preconditions not met | `check_rebase_preconditions` refused -- most often a rebase left in progress on disk by an earlier interrupted attempt. |
| HEAD read failed | `git` could not report `HEAD`; the underlying error is named in the recorded reason. |

When the fast-forward itself cannot land, the integration makes **up to
three attempts** within the same seam -- the first try plus at most two
retries: each retry re-reads the target from origin and recomputes the
integration onto the moved tip. Retryable
causes are a target that advanced concurrently (not an ancestor,
`merge --ff-only` refused, compare-and-swap mismatch) and a failed
`git worktree list` query -- the last of which fails closed rather than
moving the shared mainline ref while a live checkout may hold it.

Immediately before the fast-forward observes the target SHA, the target
is re-read from origin a second time (the first read happens when the
integration context is resolved). This matters because the rebase, the
endpoint merge and any agent conflict resolution can take minutes,
during which other agents keep landing on the same mainline. The
outcome of that refresh -- `refreshed from origin`, `already current`,
`no origin remote`, `no remote branch`, `no local branch`,
`origin unreachable`, `diverged from origin`,
`lost a concurrent refresh race`, `fetch disabled` -- is recorded on
the run state and rendered in the
`auto-integrate:` line as `[target refresh: <outcome>]`, so a landing
computed against a stale pointer is never silent. The refresh itself
stays fail-open: an unreachable remote degrades to local-only
integration and never fails the run.

### `[general.workflow]`

| Key | Default | Description |
|-----|---------|-------------|
| `checkpoint_enabled` | `true` | Enable checkpoint/resume support |
| `unsafe_mode` | `false` | Merge Ralph Workflow MCP into the agent's existing MCP config instead of overwriting it. Mirrors the `--unsafe-mode` CLI flag. |

### `[prompt_helper]`

Configuration for the interactive prompt-refinement helper launched by `ralph --prompt-helper` or `ralph-prompt`.

| Key | Default | Description |
|-----|---------|-------------|
| `agent` | _(none)_ | Agent name to use for the prompt-helper session. Omitting this setting causes Ralph Workflow to use the first configured agent in `[agents.*]`. An explicitly named but unavailable agent raises an error instead of silently falling back. |

The helper does not expose drain configuration, fallback chains, or agent chains — it uses a single interactive agent with an internal standalone session only. See the [CLI Reference](cli.md) for usage.

## Agent chains and drains

Most operator customization happens in `[agent_chains]` and `[agent_drains]` inside `ralph-workflow.toml`:

```toml
[general]
max_retries = 3
retry_delay_ms = 1000

[agent_chains]
planning = ["claude/opus"]
development = ["agy", "opencode/minimax/MiniMax-M2.7-highspeed", "codex", "claude/sonnet"]
analysis = ["opencode/openai/gpt-5.4"]
commit = ["claude/haiku"]

[agent_drains]
planning = "planning"
planning_analysis = "analysis"
development = "development"
development_analysis = "analysis"
development_commit = "commit"
```

Valid agent names include `claude`, `codex`, `opencode`, `nanocoder`, `agy`, `pi`, and `cursor`. Codex supports `codex/<model>` plus an optional `[effort=low|medium|high|xhigh]` suffix (for example, `codex/gpt-5.3-codex[effort=high]`), which maps to `--model` and Codex's reasoning-effort override. Cursor supports the same `<agent>/<model>` dynamic-alias syntax as the other model-addressable agents (e.g. `cursor/auto`, `cursor/gpt-5.3-codex-high`, `cursor/claude-sonnet-5-thinking`); the full id after `cursor/` is preserved verbatim in the `--model` flag, including bracket parameterization. Nanocoder supports the same direct-agent syntax for provider/model routing (e.g. `nanocoder/ollama/llama3.1`).

In practice: **chains** define fallback order for one kind of work; **drains** map workflow steps to those chains. Multiple drains can point at the same chain, which lets you change agent policy without rewriting the workflow itself.

### Per-agent CLI flags

Each built-in agent has a documented CLI flag shape in `ralph/policy/defaults/ralph-workflow.toml` (`[agents.<name>]`). The per-agent flag tables and compatibility caveats (CCS/GLM, ZhipuAI, Aider, Gemini CLI) live on [Agent Compatibility](agent-compatibility.md).

### `[agents.*] subagent_capability`

Each entry under `[agents.<name>]` accepts an optional `subagent_capability` switch that controls whether the agent's native sub-agent / task tooling is used to dispatch parallel work declared in a plan's `work_units` / `parallel_plan` block. The default value depends on the resolved transport:

| Agent transport | Default `subagent_capability` |
|-----------------|-------------------------------|
| `claude` | `true` |
| `claude_interactive` | `true` |
| `codex` | `None` — agent decides at runtime |
| `opencode` | `None` — agent decides at runtime |
| `nanocoder` | `None` — agent decides at runtime |
| `agy` | `None` — agent decides at runtime |
| `pi` | `None` — agent decides at runtime |
| `generic` | `None` — agent decides at runtime |

The override precedence is the same as every other Ralph Workflow setting: **CLI flags > project-local `.agent/ralph-workflow.toml` > user-global `~/.config/ralph-workflow.toml` > bundled defaults** (see the precedence list at the top of this page). Set the switch explicitly to override the transport-inferred default — for example, to force a Claude Code run to be sequential without changing every other Claude setting:

```toml
[agents.claude]
subagent_capability = false
```

This is the documented escape hatch: Ralph-managed fan-out stays dormant in this build, and the bundled default never falls back to it automatically. See [Parallel Mode](advanced-pipeline-configuration.md#parallel-execution-agent-driven) for the full agent-driven parallelism model.

## User stories: what to edit for common goals

### I want Ralph Workflow to use different coding agents

Edit `ralph-workflow.toml` → `[agent_chains]`.

### I want one repo to behave differently from my defaults

Create or edit `.agent/ralph-workflow.toml`.

### I want to change the workflow shape itself

Edit `.agent/pipeline.toml`, not `ralph-workflow.toml`. Then read [Advanced Pipeline Configuration](advanced-pipeline-configuration.md).

### I want to enable or customize MCP / web tools

Edit `ralph-workflow-mcp.toml` or `.agent/mcp.toml`. Then read [Advanced MCP Configuration](advanced-mcp-configuration.md).

### I want to change artifact contracts, decision vocabularies, or summary file outputs

Edit `.agent/artifacts.toml`. Then read [Advanced Artifact Configuration](advanced-artifact-configuration.md).

### I want to understand what my policy now does after editing it

Run `ralph --check-policy` followed by `ralph --explain-policy`.

### I broke my config and want to get back to a known-good baseline

Run `ralph --regenerate-config`. Ralph Workflow backs up overwritten files with a `.bak` suffix.

## `pipeline.toml` in plain language

`pipeline.toml` defines the workflow shape Ralph Workflow uses for a run. The top-level ideas are:

- `entry_phase` — where the run starts
- `terminal_phase` — what counts as successful completion
- `[phases.<name>]` — the individual steps in the workflow
- transitions — where Ralph Workflow goes next on success, failure, or loopback
- counters and budgets — how Ralph Workflow limits iteration and retry behavior
- post-commit routes — what happens after a commit-producing step
- parallel execution — whether independent work units can fan out concurrently

The development phase supports a proof policy block:

```toml
[phases.development.artifact_proof_policy]
require_plan_proof = true
require_analysis_proof = true
```

Each phase can declare a `display_style` override to control its banner colour. Available theme keys include `theme.phase.planning`, `theme.phase.development`, `theme.phase.development_analysis`, `theme.phase.commit`, and others defined in `ralph.display.theme`.

## `artifacts.toml` in plain language

`artifacts.toml` defines the typed outputs Ralph Workflow expects from each drain (`drain`, `artifact_type`, `decision_vocabulary`, `prompt_template`, `markdown_summary_path`, `artifact_json_path`). See [Advanced Artifact Configuration](advanced-artifact-configuration.md) for the deeper reference.

## `mcp.toml` in plain language

`mcp.toml` configures external tool servers and web-capability integrations (`stdio` / `http` MCP servers, web search backends, web-visit / readable-page fetching, advanced crawling like Crawl4AI). See [Advanced MCP Configuration](advanced-mcp-configuration.md).

## Inspecting the active policy

`ralph --explain-policy` prints a human-readable summary of the active policy bundle, and `ralph --check-policy` runs a faster pass/fail validation. Use both when you want to know what a pipeline will do without running a real workflow.

### `ralph --explain-policy`

```bash
ralph --explain-policy
ralph --explain-policy --explain-policy-dir /path/to/policy/dir
```

This reads the active `pipeline.toml` (project-local `.agent/pipeline.toml` when present, otherwise the bundled defaults) and prints a structured summary to stdout. To inspect a custom policy directory, pass `--explain-policy-dir`.

### `ralph --check-policy`

```bash
ralph --check-policy
ralph --check-policy --explain-policy-dir /path/to/policy/dir
```

This validates the same policy source as `--explain-policy` and prints a brief summary of both the authored block model and the compiled runtime phases:

```
Policy OK: /path/to/.agent
  entry block: developer_iteration
  blocks: 10
  lifecycle completion phases: 1
  phases: 10
  drains: 11
  artifact contracts: 6
  loop counters: 3
  budget counters: 1
  workflow fallbacks: 0
  terminal failure phase: failed_terminal
```

Exit codes: 0 = valid, 2 = `PolicyValidationError`, 1 = other error.

### What the explanation covers

The explanation covers the ASCII workflow diagram, the entry block, the entry phase, the terminal phase, the authored blocks, the lifecycle completion markers, each compiled runtime phase with role and drain, the loop counters, the budget counters, the terminal outcomes, the parallel execution source, and the recovery cycle cap.

### Why the diagram matters

Every routing decision the pipeline makes traces back to a single declared field in `pipeline.toml`. When a run routes somewhere unexpected, run `ralph --explain-policy` and find the corresponding `Explanation:` sentence — it names the exact policy field that produced the route. If the field is wrong, update `pipeline.toml`; if the field is correct but the runtime ignores it, that is a bug. The explanation output is deterministic for the same `pipeline.toml`; pin it in a review artifact, CI log, or runbook to record the exact workflow a run used.

## When to read further

- [Concepts](concepts.md) — terms like phase, drain, and artifact
- [CLI Reference](cli.md) — runtime flags and shortcuts
- [Advanced Pipeline Configuration](advanced-pipeline-configuration.md) — phases, routing, counters, recovery, and fan-out
- [Advanced Artifact Configuration](advanced-artifact-configuration.md) — artifact contracts, decision vocabularies, and summaries
- [Advanced MCP Configuration](advanced-mcp-configuration.md) — MCP servers, search, crawl, and web tooling
- [Developer Reference](developer-internals.md) — implementation-oriented detail
- [End-User Stories](agent-compatibility.md) — common user goals and the shortest docs path for each one
## Type checking and tooling

The maintained Python package enforces strict mypy on every supported
configuration. The strict-typing contract lives in
`ralph-workflow/mypy.ini`, with the no-plugin Pydantic contract (no
upstream Pydantic typing plugin; solve Pydantic `Any` leaks with
first-party typed helpers and adapters instead) and the strict flags
`disallow_any_explicit`, `disallow_any_decorated`,
`disallow_any_unimported`, `disallow_any_expr`, `strict_equality`,
`warn_return_any`, `warn_unused_ignores`, `warn_unused_configs`, and
`enable_error_code = ignore-without-code`.

### Suppression policy

- Test files must contain **zero** `# type: ignore` or `# pyright:`
  comment suppressions.
- Runtime code may carry a suppression only with the exact policy
  reason suffix from `docs/agents/type-ignore-policy.md`.
- Prefer a typed helper, guard, adapter, or `cast(...)` first.

### Verification

Run `cd ralph-workflow && make verify` for the canonical gate. The gate
runs the docs build, ruff, mypy --strict, the 60-second-capped
pytest suite, and the audit scripts. See
[docs/agents/verification.md](https://codeberg.org/RalphWorkflow/Ralph-Workflow/src/branch/main/docs/agents/verification.md)
for the full ordered step list, the combined test budget, and the
non-circumvention rules.
