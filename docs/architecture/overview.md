# Architecture overview — Python runtime

This page is the entry point for understanding Ralph Workflow's current
Python-runtime architecture. It maps each subsystem to what it owns, what
it depends on, what it must never bypass, and which tests or audits
protect the contract.

> For archived Rust-era architecture material, see
> [`../legacy-rust/README.md`](../legacy-rust/README.md).

## Layered architecture

Ralph Workflow is structured in seven layers. Each layer is testable in
isolation:

```text
+--------------------------------------------------------------+
| CLI surface (ralph/cli/main.py)                             |
|   - Typer app, ~33 flags                                     |
|   - Each flag is a thin wrapper that calls a command module   |
+--------------------------------------------------------------+
                            |
                            v
+--------------------------------------------------------------+
| Command handlers (ralph/cli/commands/)                       |
|   - diagnose, init, run, commit, smoke, etc.                 |
|   - Build PipelineDeps, hand off to runner                   |
+--------------------------------------------------------------+
                            |
                            v
+--------------------------------------------------------------+
| Pipeline runner (ralph/pipeline/)                           |
|   - Pure determine_next_effect(state) -> Effect               |
|   - Pure reducers + imperative effects                        |
|   - Writes checkpoints; reads policy                         |
+--------------------------------------------------------------+
                            |
        +-------------------+-------------------+
        v                                       v
+-----------------------------+      +-----------------------------+
| Phase handlers              |      | Parallel workers            |
| (ralph/phases/)             |      | (ralph/pipeline/parallel/)  |
|   - planning                |      |   - worker manifest         |
|   - development             |      |   - worker_runtime          |
|   - review                  |      |   - worker_session          |
|   - commit                  |      |                             |
|   - verification            |      |                             |
+-----------------------------+      +-----------------------------+
        |
        v
+--------------------------------------------------------------+
| Agent invocation (ralph/agents/)                             |
|   - AgentRegistry -> AgentConfig -> CommandBuilder           |
|   - IdleWatchdog + PostExitWatchdog                          |
|   - Parsers, executor, subprocess runner                     |
+--------------------------------------------------------------+
        |
        v
+--------------------------------------------------------------+
| MCP server (ralph/mcp/)                                      |
|   - Tool surface (exec, git_read, workspace, artifact, ...)  |
|   - Artifact submission via canonical_submit                 |
|   - Bounded timeouts on every blocking call                  |
+--------------------------------------------------------------+
                            |
                            v
+--------------------------------------------------------------+
| Verifier (ralph/verify.py + ralph/testing/)                  |
|   - ruff, mypy, make test, 14 audit_*.py scripts             |
|   - 60s combined test budget (immutable)                     |
+--------------------------------------------------------------+
```

## Subsystem boundaries

### CLI surface (`ralph/cli/main.py`)

**Owns:** flag parsing, command dispatch, top-level error presentation.

**Depends on:** the command modules under `ralph/cli/commands/`.

**Must never:** bypass the canonical artifact submission path, spawn
subprocesses directly (always go through `ralph/agents/`).

**Protected by:** `tests/test_cli_*.py`; `audit_lint_bypass.py` flags any
broad per-file-ignores that would weaken CLI lint rules.

### Command handlers (`ralph/cli/commands/`)

**Owns:** translating user intent into a `PipelineDeps` bundle plus a
configured pipeline invocation.

**Depends on:** `ralph/pipeline/factory.build_default_pipeline_deps`,
`ralph/agents/registry.AgentRegistry`, `ralph/config/loader.load_config`.

**Must never:** write artifacts outside the canonical path; call
`time.sleep` for backoff (use the watchdog contract instead).

**Protected by:** `tests/test_cli_commands_*.py`,
`audit_artifact_submission_canonical_path.py`,
`audit_mcp_timeout.py`.

### Pipeline runner (`ralph/pipeline/`)

**Owns:** the run lifecycle: planning → development → review → commit →
recovery. Reads policy; emits effects; reduces events into a new
`PipelineState`. The orchestrator is a **pure** `determine_next_effect`
function — the imperative effects live in `ralph/pipeline/effects/`.

**Depends on:** `ralph/policy/` for declarations, `ralph/phases/` for
phase handlers, `ralph/agents/` for invocation, `ralph/mcp/artifacts/` for
artifact submission.

**Must never:** skip verification, swallow reducer exceptions, write
checkpoints outside the canonical path.

**Protected by:** `tests/test_pipeline_*.py`,
`audit_parallelization_dormant.py`.

### Phase handlers (`ralph/phases/`)

**Owns:** per-phase logic: prompt preparation, artifact verification,
phase-specific routing. Each phase is a thin wrapper around the agent
invocation plus the artifact contract.

**Depends on:** `ralph/pipeline/state`, `ralph/agents/registry`,
`ralph/mcp/artifacts/canonical_submit`.

**Must never:** declare a phase `done` without an artifact satisfying the
phase's declared contract.

**Protected by:** `tests/test_phases_*.py`.

### Parallel workers (`ralph/pipeline/parallel/`)

**Owns:** the opt-in same-workspace worker bootstrap path. Each worker
gets its own manifest, prompt dump, checkpoint, and multimodal sidecar
under `.agent/workers/<unit_id>/`. Ralph-managed fan-out is dormant in
the bundled default; the bundled `pipeline.toml` ships with
`dispatch_mode = "agent_subagents"` so the executing agent is the actor
that dispatches its own sub-agents.

**Depends on:** `ralph/pipeline/parallel/worker_manifest`,
`ralph/pipeline/parallel/worker_runtime`,
`ralph/pipeline/parallel/worker_session`.

**Must never:** reuse the parent run's checkpoint path, share workspace
state between workers.

**Protected by:** `tests/test_parallel_mode_*.py`,
`audit_parallelization_dormant.py`.

### Agent invocation (`ralph/agents/`)

**Owns:** translating an `AgentConfig` into a subprocess invocation,
classifying output, running the four-channel idle watchdog, and
post-exit cleanup.

**Depends on:** `ralph/agents/registry`, `ralph/agents/parsers/*`,
`ralph/agents/idle_watchdog/idle_watchdog.py`,
`ralph/agents/idle_watchdog/_post_exit_watchdog.py`,
`ralph/process/`.

**Must never:** call `time.sleep` for backoff (use the watchdog);
authenticate the agent (the trust boundary is documented in
`ralph-workflow/docs/sphinx/agents.md`).

**Protected by:** `tests/test_agents_*.py`,
`tests/test_idle_watchdog_*.py`, `audit_mcp_timeout.py`.

### MCP server (`ralph/mcp/`)

**Owns:** the tool surface exposed to agents and the artifact submission
contract. Every blocking call has a bounded timeout. The artifact
submission is mediated by `submit_artifact_canonical`.

**Depends on:** `ralph/mcp/artifacts/canonical_submit`,
`ralph/mcp/artifacts/contract`, `ralph/mcp/transport/*` (per-agent
upstream configuration).

**Must never:** bypass the canonical submission path; perform an
unbounded blocking call; accept agent credentials.

**Protected by:** `tests/test_mcp_*.py`,
`audit_mcp_timeout.py`, `audit_artifact_submission_canonical_path.py`,
`tests/test_artifact_submission_canonical_path.py`.

### Verifier (`ralph/verify.py` and `ralph/testing/`)

**Owns:** the make verify contract — ruff, mypy, pytest under the 60s
combined budget, and 14 `audit_*.py` scripts that detect circumvention.

**Depends on:** all of the above.

**Must never:** permit a check to be weakened silently. Every bypass
requires a documented allowlist entry.

**Protected by:** `tests/test_verify_invariants.py`,
`tests/test_verify_budget_real_time.py`, the import-time `if`/`raise`
checks in `ralph/verify.py` that survive `python -O`.

## Cross-cutting invariants

These invariants cut across layers and are protected by tests in
multiple places:

| Invariant                                                | Owner           | Audit                                          |
| -------------------------------------------------------- | --------------- | ---------------------------------------------- |
| 60s combined test budget                                 | `ralph/verify`  | `test_verify_invariants`                       |
| Lint rules cannot be silently weakened                   | CLI             | `audit_lint_bypass`                            |
| Mypy rules cannot be silently weakened                   | All             | `audit_typecheck_bypass`                       |
| Tests must not perform real I/O or sleep                 | Tests           | `audit_test_policy`                            |
| MCP calls must carry bounded timeouts                    | MCP             | `audit_mcp_timeout`                            |
| Mutable collections must carry size caps                 | All             | `audit_resource_lifecycle`                     |
| Artifacts must go through the canonical path            | MCP, pipeline   | `audit_artifact_submission_canonical_path`     |
| Ralph-managed fan-out is dormant                         | Pipeline        | `audit_parallelization_dormant`                 |
| Watchdog R1–R8 invariants                                | Agents          | `audit_watchdog_drift`                         |
| Agent module state                                       | Agents          | `audit_agent_module_state`                     |
| Agent internal paths                                     | Agents          | `audit_agent_internal_paths`                    |
| Skill auto-commit                                        | Skills          | `audit_skill_auto_commit`                      |
| Agent registry sync                                      | Agents          | `audit_agent_registry_sync`                    |
| DI seam                                                  | Pipeline        | `audit_di_seam`                                |
| Activity-aware watchdog                                  | Agents          | `audit_activity_aware_watchdog`                |
| Public-claim fabrication                                 | All public docs | `fabrication_guard.py` (3 levels)              |

## Data flow

A run flows like this:

1. **CLI** parses flags and dispatches to the appropriate command module.
2. **Command** loads configuration, builds `PipelineDeps`, invokes
   `PipelineRunner`.
3. **Runner** materializes the initial `PipelineState`, calls
   `determine_next_effect` to get the first effect.
4. **Phase handler** prepares the prompt via the policy-declared template.
5. **Agent invocation** spawns the subprocess, runs the IdleWatchdog,
   parses output, and reports the result.
6. **Artifact submission** validates the result against the phase's
   declared contract and stores it via `submit_artifact_canonical`.
7. **Reducer** updates `PipelineState` based on the artifact.
8. **Effect router** consults policy and decides the next effect.
9. Steps 4–8 repeat until a terminal outcome (`done`, `blocked`,
   `budget-exceeded`, `regression`) is reached.
10. **Checkpoint** is written; the terminal is announced via
    `declare_complete`.

## Extension points

Three extension points compose with policy:

1. **Custom agent registration** — see
   [`ralph-workflow/docs/agents/adding-a-new-agent.md`](../../ralph-workflow/docs/agents/adding-a-new-agent.md).
   Validate the runtime can find your agent on `PATH` before declaring it
   available.
2. **Custom capability bundles** — extend the capability system with new
   skills or web helpers. Validate via `ralph --diagnose`.
3. **Custom MCP upstreams** — wire a new MCP server via the `mcp.toml`
   surface. Validate via `ralph --check-mcp`.

Every extension must preserve the cross-cutting invariants above. Any
extension that would weaken a check requires a documented allowlist entry
and an explicit justification.

## Related

- [`pipeline-lifecycle.md`](pipeline-lifecycle.md)
- [`event-loop-and-reducers.md`](event-loop-and-reducers.md)
- [`../../ralph-workflow/docs/sphinx/parallel-mode.md`](../../ralph-workflow/docs/sphinx/parallel-mode.md)
- [`ralph-workflow/docs/sphinx/ralph-loop.md`](../../ralph-workflow/docs/sphinx/ralph-loop.md)
- [`ralph-workflow/docs/sphinx/policy-driven-pipeline.md`](../../ralph-workflow/docs/sphinx/policy-driven-pipeline.md)
- [`ralph-workflow/docs/sphinx/phase-routing.md`](../../ralph-workflow/docs/sphinx/phase-routing.md)
- [`ralph-workflow/docs/sphinx/artifact-lifecycle.md`](../../ralph-workflow/docs/sphinx/artifact-lifecycle.md)
- [`ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md`](../../ralph-workflow/docs/sphinx/watchdogs-and-timeouts.md)
- [`ralph-workflow/docs/sphinx/verification-model.md`](../../ralph-workflow/docs/sphinx/verification-model.md)
- [`ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md`](../../ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md)
- [`../legacy-rust/README.md`](../legacy-rust/README.md)