# Concepts

> **New to Ralph Workflow?** See [Getting Started](getting-started.md) first if you want the setup flow before the terminology.

This page explains the terms and mental models you will see most often when running Ralph Workflow.

## The simplest mental model

The core Ralph loop is **plan → build → verify → fix**; see [Ralph loop](#ralph-loop) for the deeper explanation.

A normal run looks like this:

1. You write the task in `PROMPT.md`
2. Ralph Workflow plans and implements the work
3. Ralph Workflow records analysis and review output during the run
4. You inspect the resulting changes, logs, and artifacts afterward

The terms below help explain how Ralph Workflow does that.

## Glossary

- **`PROMPT.md`** — the task file Ralph Workflow reads when you start a run. It should describe the goal, the acceptance criteria, and any important constraints or context. If it still contains the starter template created by `ralph --init`, Ralph Workflow refuses to run until you replace it with a real task.
- **Phase** — one step in the workflow. Common phases include planning, development, review, fix, commit, and completion. Each phase has one job.
- **Agent** — an external AI coding tool Ralph Workflow runs for a phase. Examples include Claude Code, Codex, OpenCode, Pi.dev, and Google Anti Gravity-based setups. Ralph Workflow does not lock you into one provider.
- **Agent chain** — an ordered fallback list of agents for one kind of work. If the first agent fails or exhausts its retries, Ralph Workflow can move to the next one instead of stopping immediately.
- **Drain** — the routing label between a phase and an agent chain. The practical takeaway: a phase is not hard-wired to one specific agent. You can change routing in config without rewriting the workflow.
- **Policy** — the configuration that defines how Ralph Workflow behaves. The main files live under `.agent/`: `.agent/pipeline.toml` (workflow phases and routing), `.agent/artifacts.toml` (artifact expectations), `.agent/ralph-workflow.toml` (optional project-local overrides for agent chains and main settings), `.agent/mcp.toml` (MCP server configuration).
- **Artifact** — a structured output produced during a phase. Each artifact is a markdown document checked against its type's boundary (sanity-only for plans) — the same readable file Ralph Workflow routes on is the file you inspect; handoff copies under `.agent/` (like `.agent/PLAN.md`) put the latest artifact at a stable path for humans and downstream agents.
- **Review output** — Ralph Workflow can record review output during the run, depending on the active policy. The important distinction: **agents** can review work while the run is in progress; **humans** inspect the completed work, logs, and artifacts afterward in their normal git workflow.
- **MCP** — **Model Context Protocol**. In day-to-day use, this is the tool layer Ralph Workflow exposes to agents so they can read files, write outputs, submit artifacts, and use other approved capabilities.
- **Checkpoint** — Ralph Workflow's saved resume state. From the human operator shell: `ralph --inspect-checkpoint` shows what would be resumed; `ralph --no-resume` ignores the saved checkpoint and starts fresh.
- **Recovery** — Ralph Workflow's built-in ability to keep a run moving through interruptions and failures. Includes retrying transient failures, falling over to the next agent in a chain, pausing safely when connectivity drops, and resuming from checkpointed state.
- **Work unit** — a recommended, best-effort extracted sub-task inside a larger plan. Independent units can run in parallel; an accepted prose plan remains executable when no unit structure is extracted.
- **Parallel mode** — lets Ralph Workflow execute multiple work units concurrently. See [Advanced Pipeline Configuration → Parallel execution (agent-driven)](advanced-pipeline-configuration.md#parallel-execution-agent-driven).
- **Transcript** — the live terminal output from a run. Answers three questions: what is Ralph Workflow doing right now, did it make progress, did anything fail or need attention?
- **Verbosity** — controls how much output Ralph Workflow prints. Use `--quiet`, `--debug`, or `--verbosity <level>`.
- **`ralph --explain-policy`** — from the human operator shell, prints a human-readable summary of the active workflow policy.

**Interrupts.** Ctrl+C during a run starts an orderly shutdown. Press Ctrl+C
a second time only when you need to force the run to exit. This two-step model
tries to preserve a clean handoff before it stops work abruptly. For the
signal-handling contract, see [Developer Internals](developer-internals.md).

---

## Ralph loop

The **Ralph loop** is the simple core pattern that Ralph Workflow composes into a stronger workflow. The original insight, attributed to
[Geoffrey Huntley (ghuntley.com/ralph)](https://ghuntley.com/ralph), is disarmingly simple: repeat a strong prompt until the model can make real progress.

The original Ralph loop is useful for tiny one-shot work. It does not scale to multi-hour engineering pipelines, and it does not survive the failure modes that show up in real software projects — bad specs, flaky tests, ambiguous requirements, context-window exhaustion.

Ralph Workflow keeps the simple core — **plan, build, verify** — and wraps it in the machinery that real engineering needs:

- a **policy bundle** that declares phases, drains, recovery, and terminal conditions
- a **phase router** that selects the right agent and capability bundle per phase
- a **four-channel watchdog** that catches idle, stuck, and crashed agents before they waste hours
- an **artifact lifecycle** that produces evidence per phase and a single terminal `development_result`
- a **verification model** that gates the whole pipeline on real checks

The simple Ralph loop is still the conceptual unit: each phase is a small plan → build → verify loop, with the artifact from one phase becoming the spec for the next. The composed workflow is **a Ralph loop of Ralph loops**.

### Inner loop vs outer loop

| Scope  | What it is                              | Lives where                                |
| ------ | --------------------------------------- | ------------------------------------------ |
| Inner  | One phase, one agent, one prompt        | `ralph/agents/invoke.py`, `ralph/phases/`  |
| Outer  | The full plan → build → verify → review pipeline | `ralph/pipeline/orchestrator.py` |

The **inner loop** is what a chat-coding user does by hand: prompt → response → iterate. The **outer loop** is what Ralph Workflow adds: structured routing, retry, recovery, and review across many inner loops.

When you read the code, the inner loop is mostly in `ralph/agents/`. The outer loop is mostly in `ralph/pipeline/` and `ralph/policy/`.

### Per-phase execution

Each agent-backed phase starts a separate agent invocation. Recovery, review,
and re-runs can start additional invocations. Artifacts carry phase output into
the next phase.

### When the simple core is enough

If your task fits one prompt and one response, the original Ralph loop is the right tool. Use Ralph Workflow when the task doesn't fit — when there are too many inner loops to manage by hand, when verification needs to be rigorous, or when you want to leave the machine running while you sleep.

---

## Policy-driven pipeline

A **policy-driven pipeline** is a runtime that follows a declared policy and fails closed when the policy is unsatisfied. Ralph Workflow's runtime takes this shape: every phase, drain, recovery rule, artifact contract, and terminal condition is **declared in policy**, not hard-coded in Python.

### Policy configuration

Ralph Workflow loads a bundled default policy and applies project-local policy
overrides without requiring Python changes.

### What lives in policy

| Section              | Declares                                                                       |
| -------------------- | ------------------------------------------------------------------------------ |
| `[blocks]`           | Which phases exist, in what order, with what drains and routes; group blocks compose them, including the development iteration loop |
| `[agents]`           | Which agent specs are available and what flags they accept                     |
| `[recovery]`         | Retry budgets, watchdog settings, and recovery transitions                     |
| `[artifacts]`        | The artifact contracts each phase must produce                                 |
| `[mcp]`              | MCP upstream configuration and transport selection                             |
| `[capabilities]`     | The capability bundle the runtime exposes                                      |

The bundled default policy lives in `ralph/policy/defaults/*.toml`.


### How the runtime uses policy

`ralph/pipeline/orchestrator.py` is a **pure `determine_next_effect`** function: given the current `PipelineState`, it consults the policy and returns the next effect to execute. The effect is then handed to the appropriate handler in `ralph/phases/`.

The runtime:

- It does **not** decide which phases exist — policy decides.
- It does **not** decide which agent handles a phase — the routing layer consults policy and returns the agent name.
- It does **not** decide what counts as recovery — policy declares the transitions and the runtime enforces them.
- It does **not** decide what artifacts are required — the artifact contract is policy, and the runtime fails the phase if it's missing.

### Fail-closed behavior

If policy is unsatisfiable (e.g. a chain references an unknown agent, or a recovery transition has no budget), the runtime **fails closed** rather than guessing. The `ralph --check-policy` command validates a policy bundle before a real run; the validation runs the same code path the runtime uses, so a green check means the runtime will not fail with a policy-shaped error during the run.

### Where policy ends and code begins

**Policy decides** what the pipeline *should* do for a given state; **code enforces** what the pipeline *must* do (atomicity, checkpoint integrity, watchdog invariants, artifact validation, budget caps). If the behavior is project- or team-specific, it belongs in policy. If it's a correctness invariant, it belongs in code with a test.

### Policy validation

Run `ralph --check-policy` after changing policy. It uses the runtime's policy
validator and reports unsatisfied phase, route, recovery, and artifact
contracts before a run starts.

---

## Phase routing

A **phase** is one unit of work in the Ralph Workflow pipeline. Each phase
names a **drain** — the routing label that binds an agent-running phase to
an agent chain through `[agent_drains.<name>]` in `agents.toml`. The
phase's result follows its **declared transitions**. Together they define
the run lifecycle.

### What a phase is

A phase has: a **name** (e.g. `planning`, `development`,
`development_commit_review`); a **drain** — the routing label that binds
the phase to an agent chain through `[agent_drains.<name>]` in `agents.toml`;
a **prompt template** — the Jinja template that materializes the phase's
prompt from the current state; an **artifact contract** — what the phase
must produce; and **transitions** — `on_success`, `on_failure`,
`on_loopback`, and `on_reject` (`ralph/policy/models/_phase_transition.py`).
In the runtime's abstract model, a pipeline is a graph of named phases
(`ralph/policy/models/_pipeline_policy.py`). To customize the bundled default
pipeline you author that graph as blocks in `pipeline.toml`: phase settings
live under `[blocks.<block>.phase]`, with transitions under
`[blocks.<block>.phase.transitions]`, as the bundled default declares them in
`ralph/policy/defaults/pipeline.toml`. A `pipeline.toml` that declares a
top-level `[phases]` table with no `blocks` and no `entry_block` is
classified as the obsolete phase-authored schema: strict validation rejects
it with a policy validation error, and a non-strict load warns and falls
back to the bundled default (`ralph/policy/loader.py`). The runtime reads
the declaration and resolves the agent chain from policy.

### What a drain is

A **drain** is the routing label between a phase and an agent chain. The
practical takeaway, matching the [glossary](#glossary): a phase is not
hard-wired to one specific agent. Phases that run an agent declare
`drain = "<name>"`, which binds through `[agent_drains.<name>]` in
`agents.toml` to an agent chain with fallback. The drain decides **who**
does the work; the phase's declared transitions decide **what happens
next**.

### The run lifecycle

A typical run of the bundled default pipeline follows this order, with the
transitions declared in `ralph/policy/defaults/pipeline.toml`:

```text
planning
  on_success → planning_analysis
planning_analysis
  on_success → development
  on_loopback → planning
development
  on_success → development_commit_review
  on_loopback → development (another iteration)
development_commit_review
  on_success → development_commit
  on_loopback → development_commit_review
  on_reject → development
  on_failure → failed_terminal
development_commit
  on_success → development_analysis
  on_failure → failed_terminal
development_analysis
  on_success → development_final_commit_review
  on_loopback → development
development_final_commit_review
  on_success → development_final_commit
  on_loopback → development_final_commit_review
  on_reject → development
  on_failure → failed_terminal
development_final_commit
  on_success → complete
  on_failure → failed_terminal
```

Only the two commit-review phases carry `on_reject`, and the two commit
phases carry only `on_success` and `on_failure`. A phase without a declared
transition for the current result fails the run, because an unset
transition means the pipeline fails.

### Runtime behavior

The runtime is a state machine over the policy you declared: each phase records
an outcome, then Ralph Workflow chooses the next permitted action. This makes
routing and recovery predictable, but it also means an invalid policy stops the
run rather than being guessed at. Use `ralph --check-policy` before a run when
you change policy. For the reducer and effect implementation, see
[Developer Internals](developer-internals.md).

### Checkpoints

The runtime persists its current `PipelineState` as a checkpoint at
`.agent/checkpoint.json` (`ralph/pipeline/checkpoint.py`). If the run is
interrupted, the next `ralph` invocation reads the checkpoint and resumes
from the saved state. `ralph --inspect-checkpoint` prints the checkpoint
contents as raw JSON.

### Fan-out

For multi-unit plans, the policy can declare `parallel_plan` or `work_units`. Parallel execution is delegated to the executing AI agent in the bundled default — Ralph-managed fan-out is dormant. See [Advanced Pipeline Configuration → Parallel execution (agent-driven)](advanced-pipeline-configuration.md#parallel-execution-agent-driven) for the opt-in contract.

### Terminal outcomes

A run ends at a terminal phase: `complete` (`terminal_outcome = "success"`)
or `failed_terminal` (`terminal_outcome = "failure"`), both declared in
`pipeline.toml`. The terminal outcome and the run's artifacts are what the
user reviews afterward.

---

## Artifact lifecycle

An **artifact** is the durable evidence a phase produces. Ralph Workflow's artifact lifecycle is what turns a phase's agent invocation into a handoff the user can inspect the next morning.

### Artifacts and transcripts

A chat transcript shows what the agent *said*. An artifact shows what the agent *did*. An artifact is the durable evidence a phase produces; policy declares an **artifact contract** per phase; the runtime validates the contract before the phase can take its `on_success` transition; a phase that fails its contract does not advance — it follows its declared `on_failure` or `on_loopback` transition.

### The artifact format

Every artifact in Ralph Workflow is a markdown document — the artifact file **is** the readable source of truth. Most types have a structural grammar; plans require only readable, non-empty text with at least ten words, no more than 4,000,000 raw UTF-8 bytes, and recognizable plan intent. Headings, IDs, ownership and dependencies are optional best-effort extraction hints, not acceptance rules. The planning analyzer judges quality, including whether independent work can run in parallel.

The contract and implementation live in:

- `ralph/mcp/artifacts/format_docs/<type>.md` — per-type format docs
- `ralph/mcp/artifacts/canonical_submit.py` — the canonical persistence path
- `ralph/mcp/artifacts/markdown/specs/` — the per-type submission boundaries and extraction rules

The submission contract is verified by `tests/test_audit_artifact_submission_canonical_path.py` and audited by `ralph.testing.audit_artifact_submission_canonical_path`.

### The submission path

Every artifact is submitted via the `ralph_submit_md_artifact` MCP tool, which applies the artifact type's submission boundary and persists it through `submit_artifact_canonical` in `ralph/mcp/artifacts/canonical_submit.py`. This is the **only** supported submission path; ad-hoc writes to the artifact store are not permitted. The path: checks the document (sanity-only for plans; structural diagnostics for other types), writes the artifact to `.agent/artifacts/<type>.md`, writes the matching handoff copy under `.agent/` when the type has one, and stamps a submission receipt keyed on the run ID.

The runtime then consults the artifact contract for the current phase and decides whether the artifact satisfies it.

### The artifact types

| Type                | Produced by               | What it must contain                                             |
| ------------------- | ------------------------- | ---------------------------------------------------------------- |
| `development_result`| development phase         | Outcome, changed files, checks run, reviewer focus                |
| `issues`            | review phase              | List of issues raised during review                              |
| `fix_result`        | fix cycle                 | Same shape as `development_result`, scoped to the fix             |
| `commit_message`    | commit phase              | Conventional-commit subject + structured payload                  |
| `commit_review`    | commit review phase       | Verdict (approve/reject), actions to delete/gitignore/git-exclude, reason citations |
| `planning_analysis_decision` | planning analysis | analysis choice for a plan section                              |
| `review_analysis_decision`   | review analysis   | analysis choice for a review section                           |
| `development_analysis_decision` | development analysis | analysis choice for a development section                |
| `smoke_test_result` | smoke-test commands       | Black-box test summary                                           |
| `product_spec`      | product spec phase        | Spec the run was built against                                   |

Each type's contract lives at `ralph/mcp/artifacts/format_docs/<type>.md`.

### Completion detection

A phase is `done` when **all** of: the agent invocation returned (no timeout, no crash); the artifact was submitted through the canonical path; the artifact satisfies the phase's declared contract; the reducer for the artifact type advances the pipeline state.

If any step fails, the phase is not done. The phase then follows its
declared `on_failure` or `on_loopback` transition instead of advancing.

### The `declare_complete` terminal

`declare_complete` is the MCP tool the executing agent calls to declare its
task complete. It writes a durable completion sentinel for the run and
emits a completion event to the pipeline; the completion gate re-validates
the submitted artifact before accepting it. The run's terminal outcome —
`complete` or `failed_terminal` — is declared in policy, and the artifacts
are the review surface. There is no "trust the transcript" path.

### The canonical submission path

The runtime guarantees that every artifact passes its type's boundary, including the sanity-only plan check, every artifact is associated with a run ID, every artifact is recorded in the audit sink, and no artifact can bypass validation.

---

## Commit review and damage gate

Before any code change is committed, the commit review phase executes as both a deterministic safety gate and an edit phase. Accidental damage—such as mass file deletion, removal of protected project files, or staged secrets—is caught and rolled back before `git commit` ever runs.

### Two-stage review architecture

1. **Stage 1 — Deterministic tripwires (engine, no agent):**
   The engine computes diff statistics between `HEAD` and the pending change. **Hard tripwires** reject automatically without consulting an agent: no reviewer agent is invoked and no prompt is materialised, so an agent cannot talk the gate out of a hard tripwire.
   - **Mass deletion:** Deleted tracked files exceed the count limit (`max_deleted_files`, default 50) or fractional limit (`max_deleted_fraction`, default 0.25 / 25%).
   - **Protected paths:** Deletion or overwrite of `.git` internals and of the core instructions (`PROMPT.md`, `AGENTS.md`); deletion of a project manifest or lockfile (`pyproject.toml`, `package.json`, `Cargo.toml`, `uv.lock`, etc.); and deletion of a top-level source or test directory entry (`src`, `tests`, `test`, `lib`, `ralph`, `app`, `pkg`).

   **Soft findings** do not reject at Stage 1; they are passed to Stage 2 as structured evidence:
   - **Out-of-scope paths:** Modified or added files outside `allowed_directories` when configured.
   - **Secrets and binaries:** Recognized secret filenames or patterns and binaries exceeding `binary_size_limit` (default 1,000,000 bytes / 1 MB) are surfaced as evidence for cleanup. When the edit step cannot untrack or ignore them, the unsafe content is still in the re-sealed proposal and the engine rejects it on its own evidence (path, size, and the policy limit).

2. **Stage 2 — Agent review:**
   When no hard tripwire fires, an agent inspects the diff and Stage 1 evidence. The agent submits a `commit_review` artifact containing:
   - `verdict: approve | reject`: Strict closed-vocabulary verdict.
   - `tree_sha`: 40-character hex SHA of the sealed proposal tree.
   - `## Actions`: Required section (it may be empty) of permitted cleanup operations (`delete_file`, `add_to_gitignore`, `add_to_git_exclude`), each written as `<action> | <value>` and executed through a strict security boundary.
   - `## Reason`: Mandatory on `reject`. The reviewer must cite concrete paths or counts present in the diff. Unverifiable citations fail as reviewer malfunctions rather than triggering rollbacks.

Commit review is **not** a code quality review—`development_analysis` owns code quality and plan adherence. Commit review answers only: *"does this diff look like accidental damage?"*

Every threshold above is a `[commit_review]` setting in the pipeline policy TOML. The full table with the bundled defaults is in [Configuration → Commit review configuration](configuration.md#commit-review-configuration).

### Intentional large deletions have no bypass

The mass-deletion tripwires measure the diff against `HEAD`. They never consult the plan, the task text, or any other signal of operator intent, so a deliberately large deletion trips the same hard rejection as an accidental one. There is no plan-based exemption: the supported way to allow a large intentional deletion is to raise `max_deleted_files` and/or `max_deleted_fraction` in the `[commit_review]` block of the pipeline policy TOML.

### Proposal sealing

To ensure review and commit operate on a consistent snapshot rather than an uncoordinated working directory:
- Working tree changes are sealed into an isolated Git tree and commit object parented on `HEAD` under `refs/ralph/proposals/<run>/<iteration>`.
- Sealing uses a temporary Git index without mutating the developer's working tree or the real `.git/index`.
- Any actions applied during review reseal the state to produce an updated `tree_sha`.
- The commit executor verifies `write-tree` matches the approved `tree_sha` before running `git commit`, ensuring only the exact approved state is committed.

### Rollback on rejection

When a proposal is rejected:
- **Preflight check:** Confirms `git rev-parse HEAD` and the proposal ref resolve.
- **Checkpoint:** Records `rollback_pending` for crash-safe resume.
- **Restore:** Resets the working tree to `HEAD` (or the run-start baseline if the run started on a dirty tree) using `git read-tree --reset -u HEAD`, removing files present in the proposal tree but absent from `HEAD`. Ignored files and `.agent/` runtime state are preserved intact. The rollback is driven by the sealed proposal ref and uses **neither `git stash` nor a saved patch file**.
- **Continuation:** The rejection reason and proposal ref are passed to the next developer prompt, and the workflow loops back to `development`.
- **Bounded retries:** Each rejection charges the iteration budget like a commit. The `commit_review_rejections` counter tracks consecutive rejections (default limit 2); the run fails once the counter reaches that limit — with the default of 2, the second consecutive rejection is terminal, without another rollback — and the rejected proposal ref is kept.
- **Timebox exhaustion:** A rejection that arrives after the cycle timebox has elapsed does not loop back to `development`. It routes to `failed_terminal` (the phase's `on_failure` target, or the recovery `failed_route` when `on_failure` is unset) and performs no rollback.

### Manual recovery

Because rejected proposals are sealed under `refs/ralph/proposals/<run>/<iteration>`, discarded changes are not lost. If a rejection was triggered in error, you can recover the sealed state directly:

```bash
git checkout refs/ralph/proposals/<run>/<iteration> -- .
```

### Phase rename and checkpoint compatibility

The current phase names in the bundled default policy (`ralph/policy/defaults/pipeline.toml`) are `planning`, `planning_analysis`, `development`, `development_commit_review`, `development_commit`, `development_analysis`, `development_final_commit_review`, `development_final_commit`, `complete`, and `failed_terminal`. Ralph Workflow performs **no checkpoint migration** for renamed phases: a checkpoint that still names a pre-rename phase is not rewritten, the run cannot route that phase, and the run fails with a clear message. Start a fresh run after upgrading.

---

## Verification model

Ralph Workflow treats verification as a **non-bypassable gate** that runs after every code-affecting phase and at the terminal of every run. The verification model is the same regardless of which agent produced the change, which phase emitted the artifact, or which policy bundle drove the run.

### What `make verify` proves

`make verify` is the canonical verification command. It runs four kinds of checks: **Lint** (`ruff check ralph/ tests/`), **Typecheck** (`mypy ralph/`), **Test** (`pytest` under the immutable 60-second combined budget), and **Audit** (the `ralph.testing.audit_*` scripts that detect circumvention of the policy and quality gates).

A clean `make verify` proves the Python code is lint-clean, type-clean, the unit and integration tests pass within the budget, and no audit invariant has been silently weakened.

A green `make verify` is a **necessary** precondition for declaring work done, but it is not sufficient: the runtime also verifies the run artifact against the phase's declared contract (see [Artifact lifecycle](#artifact-lifecycle)).

### The 60-second combined test budget

The test budget is **60 seconds, combined, ABSOLUTE and IMMUTABLE**. It keeps
feedback fast enough to trust, so a timeout means the test design needs repair,
not a larger allowance. The same rule applies regardless of how tests are
arranged or invoked.

### The audit guardrails

Audits prevent checks from being weakened just to get a green result. Together
with linting, type checking, and tests, they make `make verify` a dependable
gate rather than a suggestion. See [Developer Internals](developer-internals.md)
for the enforcement mechanism and audit inventory.

### Non-circumvention rules

The verification model has explicit non-circumvention rules. Highlights: lint, typecheck, and test checks cannot be weakened to get green; the MCP timeout contract cannot be bypassed without an inline marker and a documented reason; resource accumulators must carry a size cap or a justified marker; the test budget cannot be circumvented by splitting or renaming; artifact submissions must go through the canonical path; the fabrication guard cannot be weakened or skipped. These are **policies**, not suggestions. Each one is enforced by an audit or a runtime check, and each bypass requires an entry in a documented allowlist.

---

## Watchdogs

Ralph Workflow uses **watchdogs** to detect stuck or crashed agents and **timeout policies** to bound how long any operation may take. Both are policy-declared; the runtime enforces them.

### Unattended runs

An unattended run by definition has no human in the loop. The runtime is the only thing watching the agents. If an agent is stuck — looping, waiting on input that never comes, crashed silently — the runtime must detect and recover before the run wastes hours. The watchdog consults policy.

### The four evidence channels

The current idle watchdog considers four evidence channels before declaring a session idle: `stdout` (agent stdout output, the baseline), `mcp_tool` (Ralph Workflow MCP tool calls and completions), `subagent` (delegated child progress, tool calls, and heartbeats), `workspace` (workspace file changes from `WorkspaceMonitor`).

The watchdog verdict is based on **demonstrated work**, not mere existence. An OpenCode subagent process that is alive but has produced no output, no tool calls, and no file changes for the configured idle window is **not** evidence of progress.

Native OpenCode subagents (the `task` tool) are a special case: `opencode run --format json` emits `step_start` when the parent delegates and then nothing until the child finishes, so the stream itself carries no evidence for the whole subagent run. Ralph Workflow therefore reads OpenCode's own session store (`$XDG_DATA_HOME/opencode/opencode.db`, default `~/.local/share`) read-only: every part a child session writes — tool call, reasoning, text — is forwarded as `subagent` evidence and resets the idle baseline exactly as a parent output line would, so a subagent that works for 20 minutes keeps the parent alive for 20 minutes. Nested subagents (a child that dispatches its own `task`, possible when an agent config grants that permission) are followed the same way. The store is resolved as the OpenCode binary resolves it, including the `OPENCODE_DB` override and channel-suffixed store names. A child that stops writing goes stale on the same schedule as any other silent channel and the standard ceilings apply; the absolute session ceiling is never extended. One limitation is inherent to the store: a single long-running MCP tool call inside the child writes nothing until it returns, so it is as silent to the probe as it is to the stream.

### Workspace change kinds

The `workspace` channel classifies every file change into one of five `WorkspaceChangeKind` values. Each kind has a configurable weight via the `agent_workspace_change_weights` config key (under `[general]`); the weight is binary — `0.0` drops the change from the verdict (it does **not** defer the `NO_OUTPUT_DEADLINE` fire), `1.0` counts as full activity.

| Kind        | What it covers (default weight)                              |
| ----------- | ------------------------------------------------------------ |
| `source`    | Source code and documentation (`1.0`)                        |
| `log`       | `*.log`, `*.tmp`, `*.bak`, `*.swp`, `*~`, `*.pyc`, `*.pyo` (`0.0`) |
| `cache`     | `.git`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `node_modules`, `.venv`, `.agent/tmp`, `.agent/raw`, `.agent/ralph-explore`, `.agent/receipts`, `.agent/prompt_history`, `.agent/artifact-formats`, `.agent/logs`, `completion_seen_*.json`, the engine-written parts of a fan-out worker unit (`.agent/workers/<unit>/{tmp,logs,handoffs}`, its `worker-manifest.json`), the runner's root-level `checkpoint.json` and the transport MCP overlays (`.agents/mcp_config.json`, `.cursor/mcp.json`, `.kimi-code/mcp.json`), and every file directly under `.agent/` (the `state.db` trio, the `.workspace-awareness.json` sidecar, `PLAN.md`, `active_runs.json`, lock files — Ralph Workflow's own bookkeeping is never agent activity) (`0.0`) |
| `artifact`  | `.agent/artifacts` (`0.0`)                                   |
| `other`     | Anything that does not match a specific rule (`0.0`)          |

The default policy is conservative: only `source` is weighted `1.0`, so quiet unattended runs that do real code work are seen as making progress while log-file churn, cache writes, and artifact writes do not falsely defer the verdict. Operators who relied on log-file activity to defer the verdict can opt in by setting `agent_workspace_change_weights = { source = 1.0, log = 1.0 }` in the `[general]` section of `ralph-workflow.toml`. The full set of kinds and their default weights is declared in `ralph/policy/defaults/recovery.toml`; override per-project via `agent_workspace_change_weights` (format: `<kind>=<weight>` entries).

### Idle deferral

While any non-stdout channel is fresher than the `agent_idle_activity_evidence_ttl_seconds` knob (under `[general]`, default `30.0`), the `NO_OUTPUT_DEADLINE` fire is **deferred** and the watchdog returns `CONTINUE`. Set the knob to `0.0` to opt out and restore the legacy stdout-only behavior.

Workspace evidence collection runs whenever a run has a `workspace_path`, regardless of whether the progress UI (`show_progress`) is enabled, so quiet unattended runs that do real file work are not falsely killed.

### The HARD_STOP diagnostic

When the watchdog decides a session is stuck, it emits a `HARD_STOP` diagnostic carrying a per-channel `evidence_summary` array with `{channel, last_at, age_seconds, counter}` entries and an `active_channel` label. The diagnostic tells a post-mortem reader exactly which channels were fresh and which were stale at the moment of the verdict.

### Absolute ceilings

Some ceilings are **absolute** — no activity can extend them: `SESSION_CEILING_EXCEEDED` (the maximum session duration) and `CHILDREN_PERSIST_TOO_LONG` (the cumulative waiting-on-child ceiling). These are checked **before** the deferral logic. No amount of fresh evidence can override them.

### Timeout policy

The timeout policy is declared in `ralph/policy/defaults/recovery.toml` and overridable per project. The runtime enforces **per-phase timeout**, **per-iteration timeout**, **MCP call timeout** (the MCP timeout contract), and **recovery budget**.

### Conflict-resolution liveness

Conflict resolution has a stricter interpretation of liveness than an ordinary agent phase. A merge or rebase resolver may work for a long time, but each recognised sign of work resets the same fixed `900`-second inactivity window: stdout activity, MCP `tools/call`, subagent progress, and weighted workspace changes. Standalone MCP calls cross from the MCP server process to the parent watchdog through an authenticated relay, so MCP-only work counts the same way as the other channels.

The conflict profile deliberately disables normal elapsed session, child-wait, startup, repetition, post-tool, process-exit, descendant-wait, cycle-timebox, and session-wrap-up cuts. Routing caps still bound completed rounds, candidate agents, and rebase stops; they do not shorten an active attempt. An optional `total_resolution_cap_seconds` is the only elapsed-time override. It is disabled by default, starts once for the whole merge resolution or multi-stop rebase, and surfaces as `OPERATOR_CAP_REACHED` when configured. Feature integration and remote reconciliation keep separate scopes on that shared identity so one path cannot spend the other's budget.

Conflict resolution is an out-of-graph drain, not a second recovery system. Failed invocations enter the same `RecoveryController.handle` path as normal agent work, so the bound chain controls retries, backoff, candidate order, and fallover. Ralph Workflow differs only where repository ownership demands it: it stages resolved paths and advances Git after repository proof. The durable identity records tips, paths, index-stage objects, scope, attempts, and landed rebase stops, so a restart resumes work and an unchanged failure is suppressed rather than paid for again.

Long resolutions publish low-cadence status with the current round or stop, latest activity kind and age, elapsed duration, and unresolved-path count. If a resolution ends, its diagnostic distinguishes `CONFLICT_INACTIVITY`, `SUPERVISION_INFRASTRUCTURE_FAILURE`, `TRANSPORT_LOOP_DETECTED`, no-progress observation, out-of-reach escalation, budget suppression, and `OPERATOR_CAP_REACHED`; it includes last activity and unresolved paths. Ralph Workflow stops relay intake and reaps the scoped agent and MCP process tree before scanning markers, continuing a rebase, committing a merge, or returning control for deterministic abort.

See [Configuration](configuration.md#conflict_resolution) to tune the documented bounds and [Troubleshooting](troubleshooting.md) when a resolution terminates.

### Bounded MCP timeouts

An unbounded MCP call hangs the MCP server thread and starves the agent of output. The `subprocess.run`/`.communicate`/`.wait` calls in `ralph/mcp/` MUST carry a `timeout=` parameter, as must `httpx.*`, `requests.*`, `urlopen`, and `socket.create_connection`. The only bypass is a structural-exemption entry in `ralph/testing/audit_mcp_timeout.py` that names the call site and its reason; a source comment does not exempt a call. The audit (`ralph/testing/audit_mcp_timeout.py`) runs under `make verify`, so a missing timeout is a hard failure, not a warning.

### Recovery

When the watchdog or a timeout fires, the runtime hands control to the recovery layer: the watchdog emits the diagnostic and marks the session as `recoverable` or `non-recoverable`; the recovery controller consults policy for the recovery budget; if budget remains, the runtime retries the phase with the recovery prompt template; if the recovery budget is exhausted, the recovery controller routes the run to the recovery `failed_route` declared in `pipeline.toml` (`ralph/recovery/controller.py`); the bundled default sets `failed_route = "failed_terminal"`. See [Recovery](recovery.md) for the full recovery controller contract.

## Deprecated: Ralph Workflow-orchestrated workers {#deprecated-ralph-orchestrated-workers}

Ralph Workflow runs a single developer agent. The worker-orchestration model —
where Ralph Workflow split a plan into work units and dispatched parallel
"worker" agents — is deprecated.

Parallelism is owned by the developer agent's own sub-agents: that agent fans
independent work out to sub-agents using the runtime's task/orchestration
tools. The developer prompt always contains sub-agent guidance,
parallel-execution instructions, and the pre-submit review — unconditionally,
with no role gate.

### Configuration and routing behavior

The bundled `dispatch_mode` is `agent_subagents`; the developer agent owns
sub-agent fan-out end to end. The deprecated `ralph_fan_out` exhibits a
two-stage compatibility behavior: `ralph_fan_out` remains parseable and
routes independent plan units through the same-workspace fan-out machinery
into `FanOutEffect` (passing `max_parallel_workers`), preserving configuration
and routing compatibility. However, routing compatibility is distinct from
successful end-to-end worker execution: downstream, the legacy worker prompt
materialization path fails closed (`ValueError` when materializing worker
prompts, because worker prompt execution is deprecated). The developer
execution path under `agent_subagents` is the only live end-to-end execution
route.

- Under `agent_subagents`, `max_parallel_workers` is published to the
  developer prompt as `DEVELOPMENT_MAX_PARALLEL_WORKERS` and used as the
  advertised per-phase ceiling for the developer's own sub-agent fan-out.
  Under the deprecated `ralph_fan_out`, it still configures `FanOutEffect`'s
  worker pool during routing, but workers cannot execute end to end.
- Non-null `work_unit` and `worker_namespace` materialization inputs fail closed
  with `ValueError` in `ralph.prompts.materialize` (the developer execution
  path is the only live entry point). They are not CLI flags and not
  operator-facing; `ralph.prompts.materialize.PromptPhaseOptions` carries them as
  fields, `ralph.prompts.materialize.materialize_prompt_for_phase` accepts them
  as keyword arguments, and `ralph.prompts.master_prompt.materialize_master_prompt`
  accepts `worker_namespace` as a keyword argument.

### Single-audience prompt contract

The developer prompt renders once per invocation, with a single audience.
There is no `IS_WORKER` role gate, no worker-only block, and no
"coordinator" prose; the parallel-execution contract, sub-agent guidance,
orchestration paragraphs, the "dispatch remaining slices in parallel"
sentence, and the pre-submit review render unconditionally.

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough
- [Recovery](recovery.md) — retries, checkpoints, and resume behavior
- [Configuration](configuration.md) — operator-facing reference
- [Advanced Pipeline Configuration](advanced-pipeline-configuration.md) — workflow graph and per-phase overrides
- [Advanced MCP Configuration](advanced-mcp-configuration.md) — MCP and web tooling
- [Advanced Artifact Configuration](advanced-artifact-configuration.md) — artifact contracts and choice vocabularies
- [Diagnostics](diagnostics.md) — pre-flight checks
- [Troubleshooting](troubleshooting.md) — common failure modes
- [Developer Internals](developer-internals.md) — implementation-oriented material
