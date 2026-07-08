# Pro support (engine-side)

> **Audience:** Ralph-Workflow-Pro maintainers and engine
> contributors who need a one-page summary of the engine's
> surface for Pro.
> **Authoritative source:**
> [Ralph-Workflow-Pro/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md](
> https://codeberg.org/RalphWorkflow/Ralph-Workflow-Pro/src/branch/main/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md)
> (lives in the Pro repository).

Ralph-Workflow-Pro is an **optional GUI layer** that runs the engine
as a subprocess. The engine exposes a small, read-only, bounded
surface so Pro can monitor (and, in advanced uses, inject custom
pipeline collaborators) without coupling the engine to Pro. The
contract is intentionally narrow: three env vars, one marker file,
one heartbeat endpoint, one state snapshot, and one DI seam.

This page is the engine's public documentation for that surface.
If you change a module under `ralph.pro_support`, update the
corresponding section here.

## Environment variables

Pro sets exactly three env vars on the engine subprocess. The
closed set is enforced by the CI drift guard in
`make verify-drift`.

| Env var | Effect on the engine | Resolver |
| --- | --- | --- |
| `RALPH_WORKFLOW_PRO` | Non-empty value enables Pro mode. | `ralph.pro_support.env.is_pro_mode` |
| `RALPH_WORKSPACE` | Overrides the workspace root. Falls back to `Path.cwd()`. | `ralph.pro_support.workspace.resolve_pro_workspace` |
| `PROMPT_PATH` | Overrides the operator-visible source prompt. Falls back to `<workspace>/PROMPT.md`. | `ralph.pro_support.prompt.resolve_effective_prompt_path` |

The engine never reads a fourth `RALPH_*` env var. The audit in
`ralph.testing.audit_mcp_timeout` covers the package, and
`make verify-drift` greps for foreign `RALPH_*` references.

## Marker file

`Pro` writes a single JSON file at
`<workspace>/.ralph/run.json`. The engine treats this file as
**strictly read-only**: it never creates, modifies, or deletes it.

Marker schema (intentionally minimal):

- `runId` (string, required) — the run identifier the engine
  includes in `/api/heartbeat` posts.
- `port` (int, optional) — the local port Pro is listening on for
  `/api/heartbeat`. Defaults to `7432` when absent.
- `heartbeatToken` (string, optional) — the bearer token to include
  in the heartbeat header. When absent, the engine falls back to a
  sidecar file at `<workspace>/.ralph/heartbeat_token`.

Reader: `ralph.pro_support.marker.read_marker_file`. Token
resolver: `ralph.pro_support.marker.read_heartbeat_token`. Port
resolver: `ralph.pro_support.marker.read_heartbeat_port` (defaults
to `7432`). All readers return `None` on any error rather than
raising, so a missing or broken marker never breaks a non-Pro
invocation that happens to share a workspace layout.

## Heartbeat

When Pro mode is active, the engine POSTs a small JSON heartbeat to
`<base_url>/api/heartbeat` every `interval_seconds` seconds (default
`5.0`). The heartbeat client is `ralph.pro_support.heartbeat.ProHeartbeatClient`.

- **Payload:** `{run_id, token, status, pid, metadata}`.
- **Hard stop on `401` / `404`:** the loop logs a warning and
  stops; the client does not retry.
- **Transient errors continue:** connection refused, timeouts, and
  5xx responses log at debug and the loop continues — a Pro
  restart or brief outage must not crash the pipeline.
- **Bounded `httpx`:** every call carries an explicit `timeout=`;
  the audit `ralph.testing.audit_mcp_timeout` catches regressions.
- **Daemon thread:** the worker is `daemon=True` so the process
  can always exit even if Pro is hung.
- **Idempotent `stop()`:** signals the worker via a
  `threading.Event`; never joins the worker (joining a daemon
  thread can block on a slow Pro server).

## Late-marker adoption

The Pro product historically assumed the marker file is present
*before* the engine starts. In practice, an engine instance may
already be running when Pro is launched. To make the engine adopt
the marker after the engine has started, the engine runs
`ralph.pro_support.watcher.ProMarkerWatcher` in a daemon thread.

- **Polls every `poll_interval_seconds` (default `2.0`).**
- **Default `sleeper` is `Event.wait(timeout=...)`**, not
  `time.sleep` — a `stop()` call from the main thread interrupts
  the wait immediately.
- **Read-only loader:** the default `marker_loader` only calls
  `read_marker_file` and `read_heartbeat_token`; it never writes
  to the marker or its sidecar.
- **Idempotent `stop()`** that does NOT join the worker, mirroring
  `ProHeartbeatClient.stop()`.

## Modular pipeline surface

Both the main pipeline and plumbing commands compose from the same
five PROMPT-mandated collaborators through `ralph.pipeline.factory.PipelineCore`.
`PipelineCore` is a frozen dataclass with exactly these fields:

- `display_context: DisplayContext`
- `model_identity: MultimodalModelIdentity | None`
- `system_prompt_materializer: MaterializeSystemPromptFn`
- `phase_prompt_materializer: PhasePromptMaterializerFn`
- `artifact_requirements_resolver: ArtifactRequirementsResolverFn`

The bridge factory is intentionally **not** part of `PipelineCore`;
it is a plumbing-only concern. Plumbing commands receive the bridge
factory as a separate parameter and route bridge startup/shutdown
through `ralph.pipeline.plumbing._bridge_lifetime.with_bridge_lifetime`.

The lean composition root is
`ralph.pipeline.factory.build_minimal_pipeline_core(config, display_context, *, model_identity=None)`.
It does **not** accept `pro_hooks`, `policy_bundle`, `recovery_sleep`,
or any extended field; those belong to the main-pipeline bundle.

## Custom pipeline DI

Pro MAY inject custom pipeline collaborators into the engine via
`ralph.pro_support.hooks.ProPipelineHooks`. The dataclass bundles
thirteen fields:

- 5 factory callables that, when supplied, REPLACE the corresponding
  runner helpers:
  - `policy_bundle_factory: Callable[[WorkspaceScope, UnifiedConfig], PolicyBundle] | None`
  - `registry_factory: Callable[[UnifiedConfig], AgentRegistry] | None`
  - `state_factory: Callable[[UnifiedConfig, AgentsPolicy, PipelinePolicy, dict[str, int] | None], PipelineState] | None`
  - `recovery_controller_factory: Callable[[PipelineState, PolicyBundle, UnifiedConfig], tuple[RecoveryController, int]] | None`
  - `marker_watcher_factory: Callable[[Path], ProMarkerWatcher] | None`
- 1 override: `policy_bundle_override: PolicyBundle | None`; when
  set, the engine skips `policy_bundle_factory` and uses the
  override directly.
- 1 passthrough: `snapshot_registry: SnapshotRegistry | None`;
  when set, the engine publishes a `PipelineStateSnapshot` to the
  registry on each reduce step.
- 6 collaborator overrides. The first 5 are applied to `PipelineCore`
  by `apply_pro_hooks_to_core`; the last is applied to the extended
  `PipelineDeps` by `build_default_pipeline_deps`:
  - `display_context: DisplayContext | None`
  - `model_identity: MultimodalModelIdentity | None`
  - `system_prompt_materializer: MaterializeSystemPromptFn | None`
  - `phase_prompt_materializer: PhasePromptMaterializerFn | None`
  - `artifact_requirements_resolver: ArtifactRequirementsResolverFn | None`
  - `recovery_sleep: Callable[[float], None] | None`

All fields are keyword-only with `None` defaults so the seam is
zero-overhead for non-Pro runs. The dataclass is
`frozen=True, slots=True`; mutations raise
`dataclasses.FrozenInstanceError`. The `to_runner_kwargs()` method
forwards exactly six entries to the engine's `run()` entry point
and never `policy_bundle_override` or `recovery_sleep` (both are
fields that `run()`/`build_default_pipeline_deps` inspects
separately).

## Pro plumbing seam

Pro plumbing consumers target the modular `PipelineCore` surface
instead of the extended `PipelineDeps` bundle. The helper
`ralph.pro_support.hooks.apply_pro_hooks_to_core(core, pro_hooks)`
returns a new `PipelineCore` with only the five PROMPT collaborators
propagated via `dataclasses.replace`. It ignores all extended fields
(`policy_bundle_override`, `policy_bundle_factory`, `registry_factory`,
`state_factory`, `recovery_controller_factory`, `marker_watcher_factory`,
`snapshot_registry`, `recovery_sleep`).

This keeps the Pro plumbing contract narrow: a Pro plumbing consumer
only needs to know about the five `PipelineCore` collaborators, while
the main pipeline continues to use `apply_pro_hooks_to_deps` for the
full extended bundle.

## State observability

Pro can monitor the engine's progress by reading a structured
snapshot of the live pipeline state on every reduce step. The
snapshot is `ralph.pro_support.state_query.PipelineStateSnapshot`.

- **`@dataclass(frozen=True, slots=True)`:** the live
  `PipelineState` remains mutable for the engine, and a Pro
  consumer of the snapshot cannot mutate engine state through it.
- **Plain `dict` copies for nested mapping fields** —
  `metrics`, `outer_progress`, `loop_iterations`, `budget_caps`
  — so the snapshot holds no reference to the live state.
- **Publisher:** `ralph.pro_support.state_query.SnapshotRegistry.publish`,
  called from the inner loop after `state = step_result` and
  before the next iteration. Pro reads via `SnapshotRegistry.get_latest()`.
- **Defensive copy on publish:** `publish` does a `dataclasses.replace`
  with shallow `dict` copies, so a future regression that
  mutated the stored snapshot would not silently corrupt the
  registry.

## Drift guards

The following CI guards fail the build if the engine regresses
away from the Pro contract:

- **No hardcoded `PROMPT.md` literal outside the resolver.** The
  resolver is the only engine path that may construct a
  source-prompt path; every other call site must go through it.
- **No `.ralph/run.json` reference outside the marker module.**
  Allowed in `ralph/pro_support/marker.py` and
  `ralph/pro_support/watcher.py` only.
- **No `time.sleep` in `ralph/pro_support/`.** Sleeps use
  `Event.wait(timeout=...)` so `stop()` interrupts them.
- **No foreign `RALPH_*` env var.** The closed set is
  `RALPH_WORKFLOW_PRO`, `RALPH_WORKSPACE`, `PROMPT_PATH`.
- **Bounded `httpx`:** `ralph.testing.audit_mcp_timeout` scans
  `ralph/pro_support/` for unbounded `httpx` calls.

A regression in any of these fails the pipeline immediately.

## Engine internals (Pro contract)

> **Audience:** Ralph Workflow contributors maintaining the engine
> side of the Ralph-Workflow-Pro integration.

This document summarises the **engine's** half of the Pro
integration contract. The full contract lives in the Pro
repository at
`Ralph-Workflow-Pro/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md`;
any change to the engine behaviour described here MUST be
coordinated with a contract update there. The full contract is
intentionally not duplicated here.

### Engine invariants

The engine commits to four invariants under Pro mode. Each
invariant is enforced by code, by a test, and (where relevant)
by a CI drift guard.

1. **The engine reads exactly three env vars**: `RALPH_WORKFLOW_PRO`,
   `RALPH_WORKSPACE`, `PROMPT_PATH`. No others.
2. **The engine treats `<workspace>/.ralph/run.json` as
   read-only.** It never creates, modifies, or deletes it.
3. **The engine POSTs `/api/heartbeat` with the contract-shaped
   payload**, and treats `401` / `404` as hard stops.
4. **The engine exits 0 on a clean run and preserves any non-zero
   exit code on failure.** All stdout/stderr output is UTF-8 and
   newline-terminated; no bare structured-JSON log lines are
   emitted.

### Contract clause → engine module → test traceability

The contract clauses are pinned by tests under
`tests/test_pro_support_*.py` and `tests/test_pro_support_contract.py`.
The full clause-to-module-to-test table lives in
`tests/test_pro_support_*.py` (the engine-internal traceability
reference for now; this page is the public surface and only the
high-level clause summary is repeated here to keep the docs
non-redundant).

### Test inventory

The full set of files that prove the engine's Pro contract: `tests/test_pro_support_env.py`, `tests/test_pro_support_workspace.py`, `tests/test_pro_support_prompt.py`, `tests/test_pro_support_marker.py`, `tests/test_pro_support_heartbeat.py`, `tests/test_pro_support_config.py`, `tests/test_orchestrator_pro_prompt_resolution.py`, `tests/test_run_loop_pro_integration.py`, `tests/test_audit_mcp_timeout.py`, `tests/test_pro_support_contract.py`, `tests/test_pro_support_watcher.py`, `tests/test_pro_support_hooks.py`, `tests/test_pro_support_state_query.py`, `tests/test_pro_support_cross_repo_marker.py`, and `tests/test_pro_support_end_to_end.py`.

### Drift detection (CI-level)

`make verify-drift` runs three guards before the test suite:

1. **No hardcoded `PROMPT.md` literal outside the resolver.** The
   resolver is the only engine path that may construct a
   source-prompt path; every other call site must go through it.
2. **No `.ralph/run.json` reference outside the marker module.**
   A regression that lets a new engine module touch the marker
   fails the pipeline.
3. **Bounded-subprocess audit (`audit_mcp_timeout`)** covers
   `ralph/pro_support/`, catching any unbounded `httpx` call in
   the heartbeat client.

A regression of any of these fails the pipeline immediately.

### Coordination rule

When a task touches the Pro↔Ralph Workflow shared boundary, the contract
is the single source of truth. Update the contract first, then
update the engine code, then update this document, then update
the contract test in `tests/test_pro_support_contract.py` so the
new behaviour is pinned. A contract change that is not pinned by
a passing test is incomplete.

## Forward-looking engine capabilities pending contract amendment

The engine has three new surfaces that the upstream contract has not yet
been amended to formalise. Until the Pro repository accepts the
engine-side handoff, these capabilities are engine-internal and NOT
contractually binding on the Pro product:

- `ProMarkerWatcher` — late marker adoption daemon thread (see
  `ralph/pro_support/watcher.py` and
  `tests/test_pro_support_watcher.py`).
- `ProPipelineHooks` — custom pipeline DI seam, 13 fields total (5
  factory kwargs + 1 policy_bundle_override + 1 snapshot_registry +
  6 collaborator overrides, see `ralph/pro_support/hooks.py` and
  `tests/test_pro_support_hooks.py`).
- `PipelineStateSnapshot` — read-only state observability (see
  `ralph/pro_support/state_query.py` and
  `tests/test_pro_support_state_query.py`).

## Cross-references

- Upstream contract (authoritative):
  [Ralph-Workflow-Pro/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md](
  https://codeberg.org/RalphWorkflow/Ralph-Workflow-Pro/src/branch/main/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md).
- Engine implementation: `ralph-workflow/ralph/pro_support/`.
- Tests: `ralph-workflow/tests/test_pro_support_*.py`,
  `tests/test_run_loop_pro_integration.py`,
  `tests/test_orchestrator_pro_prompt_resolution.py`.
