# Concepts

> **New to Ralph Workflow?** See [Getting Started](getting-started.md) first if you want the setup flow before the terminology.

This page explains the terms and mental models you will see most often when running Ralph Workflow. The original six concept pages were merged into this single home so each topic lives in exactly one place.

## The simplest mental model

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
- **Artifact** — a structured output produced during a phase. Each artifact is a validated markdown document — the same readable file Ralph Workflow routes on is the file you inspect; handoff copies under `.agent/` (like `.agent/PLAN.md`) put the latest artifact at a stable path for humans and downstream agents.
- **Review output** — Ralph Workflow can record review output during the run, depending on the active policy. The important distinction: **agents** can review work while the run is in progress; **humans** inspect the completed work, logs, and artifacts afterward in their normal git workflow.
- **MCP** — **Model Context Protocol**. In day-to-day use, this is the tool layer Ralph Workflow exposes to agents so they can read files, write outputs, submit artifacts, and use other approved capabilities.
- **Checkpoint** — Ralph Workflow's saved resume state. From the human operator shell: `ralph --inspect-checkpoint` shows what would be resumed; `ralph --no-resume` ignores the saved checkpoint and starts fresh.
- **Recovery** — Ralph Workflow's built-in ability to keep a run moving through interruptions and failures. Includes retrying transient failures, falling over to the next agent in a chain, pausing safely when connectivity drops, and resuming from checkpointed state.
- **Work unit** — a sub-task inside a larger plan that can be executed independently. When planning finds multiple independent pieces of work, Ralph Workflow can split them into work units and run them in parallel.
- **Parallel mode** — lets Ralph Workflow execute multiple work units concurrently. See [Advanced Pipeline Configuration → Parallel execution (agent-driven)](advanced-pipeline-configuration.md#parallel-execution-agent-driven).
- **Transcript** — the live terminal output from a run. Answers three questions: what is Ralph Workflow doing right now, did it make progress, did anything fail or need attention?
- **Verbosity** — controls how much output Ralph Workflow prints. Use `--quiet`, `--debug`, or `--verbosity <level>`.
- **`ralph --explain-policy`** — from the human operator shell, prints a human-readable summary of the active workflow policy.

**Interrupt dispatch.** When a user presses Ctrl+C during a run, the
`InterruptDispatcher` (in `ralph.interrupt.dispatcher`) coordinates orderly
shutdown: the first SIGINT routes through
`InterruptController.begin_interrupt(kill_label='invoke:')` to send SIGTERM
to the agent's process group, then a poll thread escalates to SIGKILL if
the agent makes no CPU-time progress within `hard_kill_budget_s` (default
1.5s). A second SIGINT calls `InterruptController.force_exit(bridge_pids=...)`
which terminates tracked processes and exits with code 130. The CLI catches
in `ralph.cli.commands.run` and `ralph.cli.main` also call the dispatcher
with `block=True`, so the orderly shutdown happens even when the interrupt
is raised outside the pipeline loop.

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

### Tradeoffs the simple core accepts

- **Latency:** each phase is a full agent invocation. The composed loop is slower than a single long session. The tradeoff buys verification, recovery, and reviewability.
- **Cost:** each phase is a billable agent call. Recovery, review, and re-runs multiply cost. The tradeoff buys not having to babysit.
- **Variance:** each phase restarts from its own artifact, so the pipeline carries variance between phases. The artifact handoff is what keeps variance bounded.

### When the simple core is enough

If your task fits one prompt and one response, the original Ralph loop is the right tool. Use Ralph Workflow when the task doesn't fit — when there are too many inner loops to manage by hand, when verification needs to be rigorous, or when you want to leave the machine running while you sleep.

---

## Policy-driven pipeline

A **policy-driven pipeline** is a runtime that follows a declared policy and fails closed when the policy is unsatisfied. Ralph Workflow's runtime takes this shape: every phase, drain, recovery rule, artifact contract, and terminal condition is **declared in policy**, not hard-coded in Python.

### Why policy is the right boundary

Two alternatives were possible:

1. **Hard-code the pipeline in Python.** Simple, but every customization becomes a code change. The default workflow becomes the only workflow.
2. **Make the user write a config from scratch.** Flexible, but every project starts with zero leverage. The first run is a research project.

Ralph Workflow picks a third shape: **the runtime follows declared policy** and ships a **bundled default policy** that is strong enough to start with. The user can override any policy section without touching Python.

### What lives in policy

| Section              | Declares                                                                       |
| -------------------- | ------------------------------------------------------------------------------ |
| `[phases]`           | Which phases exist, in what order, with what drains and routes                 |
| `[agents]`           | Which agent specs are available and what flags they accept                     |
| `[pipeline]`         | The composition of phases, including the development iteration loop            |
| `[recovery]`         | Retry budgets, watchdog settings, and recovery transitions                     |
| `[artifacts]`        | The artifact contracts each phase must produce                                 |
| `[mcp]`              | MCP upstream configuration and transport selection                             |
| `[capabilities]`     | The capability bundle the runtime exposes                                      |

The bundled default policy lives in `ralph/policy/defaults/*.toml`.


### How the runtime uses policy

`ralph/pipeline/orchestrator.py` is a **pure `determine_next_effect`** function: given the current `PipelineState`, it consults the policy and returns the next effect to execute. The effect is then handed to the appropriate handler in `ralph/phases/`.

The runtime is intentionally thin:

- It does **not** decide which phases exist — policy decides.
- It does **not** decide which agent handles a phase — the routing layer consults policy and returns the agent name.
- It does **not** decide what counts as recovery — policy declares the transitions and the runtime enforces them.
- It does **not** decide what artifacts are required — the artifact contract is policy, and the runtime fails the phase if it's missing.

### Why the runtime fails closed

If policy is unsatisfiable (e.g. a chain references an unknown agent, or a recovery transition has no budget), the runtime **fails closed** rather than guessing. The `ralph --check-policy` command validates a policy bundle before a real run; the validation runs the same code path the runtime uses, so a green check means the runtime will not fail with a policy-shaped error during the run.

### Where policy ends and code begins

The boundary is intentional: **policy decides** what the pipeline *should* do for a given state; **code enforces** what the pipeline *must* do (atomicity, checkpoint integrity, watchdog invariants, artifact validation, budget caps). If the behavior is project- or team-specific, it belongs in policy. If it's a correctness invariant, it belongs in code with a test.

### The tradeoffs

The composed shape accepts three costs: a **two-file minimum** (even the smallest customization requires a `ralph-workflow.toml` next to your `PROMPT.md`), **validation is mandatory** (every policy change runs through the same validator the runtime uses), and **defaults are versioned** (the bundled policy ships with Ralph Workflow; changes between versions are documented in the changelog). The trade is **legibility**: a reader can answer "why did this run route through this agent?" by reading one policy file, not by reading runtime code.

---

## Phase routing

A **phase** is one unit of work in the Ralph Workflow pipeline. A **route** is the decision about which agent handles the phase. A **drain** is the named terminal condition a phase produces. Together they define the run lifecycle.

### What a phase is

A phase has: a **name** (e.g. `planning`, `development`, `review`); a **route** — which agent handles it (e.g. `claude-headless`); a **prompt template** — the Jinja template that materializes the phase's prompt from the current state; an **artifact contract** — what the phase must produce; a **drain set** — the terminal conditions the phase can return. Phases are declared in policy under `[phases]` and `[phases.<name>]`. The runtime reads the declaration and binds the route to an agent via `ralph/agents/registry.py`.

### What a drain is

A **drain** is a named terminal condition that a phase can end in: `done` (phase produced the required artifact), `fix-needed` (downstream phase should route to a fix cycle), `blocked` (phase cannot complete without human intervention), `retry` (phase hit a transient failure, runtime should re-attempt). Drains are declared in policy under `[phases.<name>.drains]`. The runtime uses the drain name to decide the next effect.

### The run lifecycle

A typical Ralph Workflow run looks like:

```text
[planning] --done--> [development] --done--> [verification] --done--> [review]
                              |                          |                |
                          fix-needed                  blocked        approve
                              |                          |                |
                              v                          v                v
                        [development-fix]          [recovery]       [commit]
```

The shape is declared entirely in policy. The runtime is a state machine that consults policy at each transition. If a transition is unspecified, the runtime fails closed with a policy validation error.

### Reducers and effects

The runtime has two complementary structures: **Reducers** — pure functions of `(state, event) -> state` that update the `PipelineState` in response to events; **Effects** — imperative actions the runtime performs in response to the new state. The split is intentional: reducers are testable in isolation (no I/O), effects are the integration points with the filesystem, agent subprocess, and MCP server. See `ralph/pipeline/reducers/` and `ralph/pipeline/effects/`.

### Checkpoints

After every reducer the runtime writes a checkpoint to `.agent/checkpoint.json` capturing: the current `PipelineState`, the drain the previous phase returned, the artifact path the previous phase produced, the agent and model the previous phase used, and the prompt template that was materialized. If the run is interrupted, the next `ralph` invocation reads the checkpoint and resumes from the last completed phase. `ralph --inspect-checkpoint` prints the current checkpoint in human-readable form.

### Fan-out

For multi-unit plans, the policy can declare `parallel_plan` or `work_units`. Parallel execution is delegated to the executing AI agent in the bundled default — Ralph-managed fan-out is dormant and retained only for future use. See [Advanced Pipeline Configuration → Parallel execution (agent-driven)](advanced-pipeline-configuration.md#parallel-execution-agent-driven) for the opt-in contract.

### Terminal outcomes

A run ends in one of four terminal outcomes: `done` (every phase produced its artifact; commit is ready), `blocked` (a phase returned `blocked`; human intervention needed), `budget-exceeded` (the retry budget or session ceiling was hit; current state is recoverable), `regression` (verification failed after retry; partial result). The terminal outcome is what the runtime hands back to the user via `declare_complete`. It is what the user reviews in the morning.

---

## Artifact lifecycle

An **artifact** is the durable evidence a phase produces. Ralph Workflow's artifact lifecycle is what turns a phase's agent invocation into a handoff the user can inspect the next morning.

### Why artifacts, not transcripts

A chat transcript shows what the agent *said*. An artifact shows what the agent *did*. For unattended runs that you review in the morning, the artifact is what you actually inspect. Ralph Workflow's policy declares an **artifact contract** per phase. The runtime validates the contract before accepting the phase as `done`. If the contract is missing, the phase returns `fix-needed` or `blocked`, not `done`.

### The artifact format

Every artifact in Ralph Workflow is a markdown document — the artifact file **is** the readable source of truth, validated against a closed per-type grammar:

- `ralph/mcp/artifacts/format_docs/<type>.md` — per-type format docs
- `ralph/mcp/artifacts/canonical_submit.py` — the canonical persistence path
- `ralph/mcp/artifacts/markdown/specs/` — the per-type markdown specs that enforce the grammar

The submission contract is verified by `tests/test_audit_artifact_submission_canonical_path.py` and audited by `ralph.testing.audit_artifact_submission_canonical_path`.

### The submission path

Every artifact is submitted via the `ralph_submit_md_artifact` MCP tool, which validates the markdown against its registered spec and persists it through `submit_artifact_canonical` in `ralph/mcp/artifacts/canonical_submit.py`. This is the **only** supported submission path; ad-hoc writes to the artifact store are not permitted. The path: validates the document against the per-type markdown spec (line-anchored diagnostics; any error rejects it), writes the artifact to `.agent/artifacts/<type>.md`, writes the matching handoff copy under `.agent/` when the type has one, and stamps a submission receipt keyed on the run ID.

The runtime then consults the artifact contract for the current phase and decides whether the artifact satisfies it.

### The artifact types

| Type                | Produced by               | What it must contain                                             |
| ------------------- | ------------------------- | ---------------------------------------------------------------- |
| `development_result`| development phase         | Outcome, changed files, checks run, reviewer focus                |
| `issues`            | review phase              | List of issues raised during review                              |
| `fix_result`        | fix cycle                 | Same shape as `development_result`, scoped to the fix             |
| `commit_message`    | commit phase              | Conventional-commit subject + structured payload                  |
| `commit_cleanup`    | commit phase              | Actions to delete / gitignore / git-exclude before commit         |
| `planning_analysis_decision` | planning analysis | analysis decision for a plan section                              |
| `review_analysis_decision`   | review analysis   | analysis decision for a review section                           |
| `development_analysis_decision` | development analysis | analysis decision for a development section                |
| `smoke_test_result` | smoke-test commands       | Black-box test summary                                           |
| `product_spec`      | product spec phase        | Spec the run was built against                                   |

Each type's contract lives at `ralph/mcp/artifacts/format_docs/<type>.md`.

### Completion detection

A phase is `done` when **all** of: the agent invocation returned (no timeout, no crash); the artifact was submitted through the canonical path; the artifact satisfies the phase's declared contract; the reducer for the artifact type advances the pipeline state.

If any step fails, the phase is not `done`. The reducer typically returns `fix-needed` (artifact missing or malformed) or `blocked` (a precondition failed) instead of advancing.

### The `declare_complete` terminal

When the final reducer decides the run is at a terminal state, the runtime calls `declare_complete`. The terminal is the single, structured handoff the user reviews: `done` (the development_result is the review surface), `blocked` (the issues artifact explains what blocked), `budget-exceeded` (the most recent artifact shows what was achieved), `regression` (the verification artifact shows what failed).

The terminal is the **only** signal the runtime hands back. There is no "trust the transcript" path. If the terminal is `done`, the run is done.

### Why the canonical path matters

The canonical submission path is audited because ad-hoc artifact writes are an attack surface: a bad artifact could advance the pipeline past verification. By making the canonical path the only supported write, the runtime guarantees every artifact is validated against its registered markdown spec, every artifact is associated with a run ID, every artifact is recorded in the audit sink, and no artifact can bypass validation.

---

## Verification model

Ralph Workflow treats verification as a **non-bypassable gate** that runs after every code-affecting phase and at the terminal of every run. The verification model is the same regardless of which agent produced the change, which phase emitted the artifact, or which policy bundle drove the run.

### What `make verify` proves

`make verify` is the canonical verification command. It runs four kinds of checks: **Lint** (`ruff check ralph/ tests/`), **Typecheck** (`mypy ralph/`), **Test** (`pytest` under the immutable 60-second combined budget), and **Audit** (the `ralph.testing.audit_*` scripts that detect circumvention of the policy and quality gates).

A clean `make verify` proves the Python code is lint-clean, type-clean, the unit and integration tests pass within the budget, and no audit invariant has been silently weakened.

A green `make verify` is a **necessary** precondition for declaring work done, but it is not sufficient: the runtime also verifies the run artifact against the phase's declared contract (see [Artifact lifecycle](#artifact-lifecycle)).

### The 60-second combined test budget — immutable

The test budget is **60 seconds, combined, ABSOLUTE and IMMUTABLE**. This is enforced by `ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS = 60.0` and tracked cumulatively across all `_BUDGET_TRACKED_STEPS` via `time.monotonic()`.

The budget cannot be circumvented by splitting tests into more suites, moving slow tests to a different suite/target/Makefile recipe, renaming test targets without updating `_KNOWN_TEST_STEP_LABELS`, raising `DEFAULT_SUITE_TIMEOUT_SECONDS` or `PYTEST_SUITE_TIMEOUT_SECONDS`, setting `RALPH_PYTEST_SUITE_TIMEOUT_SECONDS` or `RALPH_PYTEST_TEST_TIMEOUT_SECONDS`, raising `_TOTAL_TEST_BUDGET_SECONDS` (blocked by import-time `RuntimeError` checks — immune to `python -O`), emptying `_KNOWN_TEST_STEP_LABELS` to hide test steps, emptying `_BUDGET_TRACKED_STEPS` to disable enforcement, or removing `'make test'` from `_KNOWN_TEST_STEP_LABELS`.

A timeout failure is a test design defect — fix the test, not the budget.

### The audit invariant set

Ralph Workflow ships with 14 audit scripts in `ralph/testing/audit_*.py` that detect circumvention: `audit_lint_bypass.py` (lint rule weakening), `audit_typecheck_bypass.py` (mypy rule weakening), `audit_test_policy.py` (real I/O or sleep in non-subprocess_e2e tests), `audit_mcp_timeout.py` (unbounded blocking calls in `ralph/mcp/`), `audit_resource_lifecycle.py` (unbounded accumulators), `audit_artifact_submission_canonical_path.py` (artifact writes not via canonical path), `audit_parallelization_dormant.py` (dormant parallel mode invariant violations), and 7 more. See `ralph/testing/audit_*.py` for the full set.

Each audit has a documented allowlist. Adding an entry to an allowlist is the **only** way to weaken a check, and the entry must cite a real justification.

### Non-circumvention rules

The verification model has explicit non-circumvention rules. Highlights: lint, typecheck, and test checks cannot be weakened to get green; the MCP timeout contract cannot be bypassed without an inline marker and a documented reason; resource accumulators must carry a size cap or a justified marker; the test budget cannot be circumvented by splitting or renaming; artifact submissions must go through the canonical path; the fabrication guard cannot be weakened or skipped. These are **policies**, not suggestions. Each one is enforced by an audit or a runtime check, and each bypass requires an entry in a documented allowlist.

### Why the verification model is strict

The verification model exists because the project has shipped bugs, stale claims, and fabricated stats. The strict checks are the response: they make the failure mode **loud** rather than silent. The 60-second budget, in particular, exists because slow tests create feedback loops that erode developer trust in the test suite. The budget forces the test design to be fast by construction, which forces production code to be testable by construction.

---

## Watchdogs

Ralph Workflow uses **watchdogs** to detect stuck or crashed agents and **timeout policies** to bound how long any operation may take. Both are policy-declared; the runtime enforces them.

### Why watchdogs matter for unattended runs

An unattended run by definition has no human in the loop. The runtime is the only thing watching the agents. If an agent is stuck — looping, waiting on input that never comes, crashed silently — the runtime must detect and recover before the run wastes hours. The original Ralph loop has no watchdog: it just keeps prompting until the model says "done". Ralph Workflow replaces this with a structured watchdog that consults policy.

### The four evidence channels

The current idle watchdog considers four evidence channels before declaring a session idle: `stdout` (agent stdout output, the baseline), `mcp_tool` (Ralph Workflow MCP tool calls and completions), `subagent` (delegated child progress, tool calls, and heartbeats), `workspace` (workspace file changes from `WorkspaceMonitor`).

The watchdog verdict is based on **demonstrated work**, not mere existence. An OpenCode subagent process that is alive but has produced no output, no tool calls, and no file changes for the configured idle window is **not** evidence of progress.

### Workspace change kinds

The `workspace` channel classifies every file change into one of five `WorkspaceChangeKind` values. Each kind has a configurable weight via the `agent_workspace_change_weights` config key (under `[general]`); the weight is binary — `0.0` drops the change from the verdict (it does **not** defer the `NO_OUTPUT_DEADLINE` fire), `1.0` counts as full activity.

| Kind        | What it covers (default weight)                              |
| ----------- | ------------------------------------------------------------ |
| `source`    | Source code and documentation (`1.0`)                        |
| `log`       | `*.log`, `*.tmp`, `*.bak`, `*.swp`, `*~`, `*.pyc`, `*.pyo` (`0.0`) |
| `cache`     | `.git`, `__pycache__`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache`, `node_modules`, `.venv`, `.agent/tmp`, `.agent/raw`, `completion_seen_*.json` (`0.0`) |
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

### Why bounded MCP timeouts are non-negotiable

An unbounded MCP call hangs the MCP server thread and starves the agent of output. The `subprocess.run`/`.communicate`/`.wait` calls in `ralph/mcp/` MUST carry a `timeout=` parameter, as must `httpx.*`, `requests.*`, `urlopen`, and `socket.create_connection`. The only bypass is an inline `# mcp-timeout-ok: <reason>` marker for a genuinely unbounded-by-design call. The audit (`ralph/testing/audit_mcp_timeout.py`) runs under `make verify`, so a missing timeout is a hard failure, not a warning.

### Recovery

When the watchdog or a timeout fires, the runtime hands control to the recovery layer: the watchdog emits the diagnostic and marks the session as `recoverable` or `non-recoverable`; the recovery controller consults policy for the recovery budget; if budget remains, the runtime retries the phase with the recovery prompt template; if budget is exhausted, the run declares `budget-exceeded` and the terminal artifact is the most recent partial artifact. See [Recovery](recovery.md) for the full recovery controller contract.

## Related pages

- [Getting Started](getting-started.md) — first-run walkthrough
- [Recovery](recovery.md) — retries, checkpoints, and resume behavior
- [Configuration](configuration.md) — operator-facing reference
- [Advanced Pipeline Configuration](advanced-pipeline-configuration.md) — workflow graph and per-phase overrides
- [Advanced MCP Configuration](advanced-mcp-configuration.md) — MCP and web tooling
- [Advanced Artifact Configuration](advanced-artifact-configuration.md) — artifact contracts and decision vocabularies
- [Diagnostics](diagnostics.md) — pre-flight checks
- [Troubleshooting](troubleshooting.md) — common failure modes
- [Developer Internals](developer-internals.md) — implementation-oriented material
