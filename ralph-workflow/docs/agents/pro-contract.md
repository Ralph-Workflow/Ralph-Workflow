# Pro contract — engine responsibilities

> **Audience:** Ralph Workflow contributors maintaining the engine
> side of the Ralph-Workflow-Pro integration.
> **Last cross-checked:** 2026-06-12 against
> `Ralph-Workflow-Pro/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md`
> (the authoritative source of truth, outside this repo).

This document summarises the **engine's** half of the Pro
integration contract. The full contract lives in the Pro
repository at
`Ralph-Workflow-Pro/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md`;
any change to the engine behaviour described here MUST be
coordinated with a contract update there.

The full contract is intentionally not duplicated here. This
document is the engine's *internal* reference: it tells a
contributor which module owns each clause, which test proves it,
and which drift-detection guard catches a regression.

## Engine invariants

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

## Contract clause → engine module → test traceability

| Contract section | Engine responsibility | Engine module | Test that proves it |
| --- | --- | --- | --- |
| §2 Spawn is fire-and-forget | Engine MUST NOT depend on parent stdio being managed. | `ralph/pipeline/run_loop.py:run` | `tests/test_run_loop_pro_integration.py::test_heartbeat_started_in_pro_mode` |
| §3 RALPH_WORKFLOW_PRO | Non-empty string is the Pro-mode signal. | `ralph/pro_support/env.py:is_pro_mode` | `tests/test_pro_support_env.py::test_is_pro_mode_true_when_env_set`, `test_pro_support_contract.py::test_section_3_env_var_set_is_exactly_three` |
| §3 RALPH_WORKSPACE | Honour when set; fall back to `cwd`. | `ralph/pro_support/workspace.py:resolve_pro_workspace` | `tests/test_pro_support_workspace.py::test_resolve_pro_workspace_uses_env_when_present`, `test_pro_support_contract.py::test_section_3_ralph_workspace_is_honoured` |
| §3 PROMPT_PATH | Honour when set; fall back to `<workspace>/PROMPT.md`. | `ralph/pro_support/prompt.py:resolve_effective_prompt_path` | `tests/test_pro_support_prompt.py::test_*`, `test_pro_support_contract.py::test_section_3_prompt_path_*` |
| §3 No additional env vars | The set is closed. | (engine-wide) | `tests/test_pro_support_config.py::test_pro_mode_does_not_introduce_extra_env_vars`, `test_pro_support_contract.py::test_section_3_helpers_do_not_read_a_foreign_env_var` |
| §4 Exit 0 on clean run | Engine returns 0 when the run completes. | `ralph/pipeline/run_loop.py:run` | `tests/test_run_loop_pro_integration.py::test_pro_mode_exit_code_zero_on_clean_completion`, `test_pro_support_contract.py::test_section_4_pro_mode_clean_run_returns_zero` |
| §4 Non-zero on failure | Engine preserves any non-zero step code. | `ralph/pipeline/run_loop.py:_execute_with_cleanup` | `tests/test_run_loop_pro_integration.py::test_pro_mode_exit_code_preserved_on_pipeline_failure`, `test_pro_support_contract.py::test_section_4_pro_mode_failure_preserves_nonzero_exit_code` + `test_section_4_pro_mode_no_silent_zero_on_failure_sweep` |
| §4 No silent `try/finally: return 0` | No regression in cleanup can swallow a non-zero code. | `ralph/pipeline/run_loop.py:_cleanup_pipeline` | `test_pro_support_contract.py::test_section_4_pro_mode_no_silent_zero_on_failure_sweep` (sweep across 10 codes) |
| §5 Marker schema | Engine reads `runId`, `port`, `heartbeatToken` from `<workspace>/.ralph/run.json`. | `ralph/pro_support/marker.py:read_marker_file`, `read_run_id`, `read_heartbeat_port`, `read_heartbeat_token` | `tests/test_pro_support_marker.py::test_*`, `test_pro_support_contract.py::test_section_6_marker_reader_*` |
| §6 Marker is read-only | Engine MUST NOT create, modify, or delete the marker. | (engine-wide; reader is open-for-reading only) | `tests/test_pro_support_marker.py::test_heartbeat_client_does_not_write_marker`, `test_pro_support_contract.py::test_section_6_heartbeat_client_does_not_create_or_modify_marker` + `test_section_6_pro_support_public_api_exposes_no_marker_writer` |
| §6 Drift detection | CI grep prevents `.ralph/run.json` references outside the marker module. | `Makefile:verify-drift` | `make verify-drift` (CI-level proof) |
| §7 Heartbeat payload | Engine POSTs `{run_id, token, status, pid, metadata}` to `/api/heartbeat`. | `ralph/pro_support/heartbeat.py:ProHeartbeatClient._build_payload` | `tests/test_pro_support_heartbeat.py::test_heartbeat_posts_expected_payload`, `test_pro_support_contract.py::test_section_7_heartbeat_posts_required_fields` |
| §7 401 / 404 hard stop | Engine MUST treat 401/404 as a hard stop and not retry. | `ralph/pro_support/heartbeat.py:_post_once` | `tests/test_pro_support_heartbeat.py::test_heartbeat_401_is_hard_stop`, `test_heartbeat_404_is_hard_stop`, `test_pro_support_contract.py::test_section_7_heartbeat_401_is_hard_stop` + `test_section_7_heartbeat_404_is_hard_stop` |
| §7 Bounded `httpx` calls | Every POST carries an explicit `timeout=`. | `ralph/pro_support/heartbeat.py:_post_once` | `tests/test_audit_mcp_timeout.py::test_audit_flags_unbounded_httpx_in_pro_support`, `test_pro_support_contract.py::test_section_7_heartbeat_request_carries_bounded_timeout` |
| §7 Heartbeat starts in Pro mode only | No heartbeat unless `RALPH_WORKFLOW_PRO` is set AND marker is valid. | `ralph/pipeline/run_loop.py:_start_pro_heartbeat_if_active` | `tests/test_run_loop_pro_integration.py::test_heartbeat_started_in_pro_mode`, `test_no_heartbeat_when_pro_mode_inactive`, `test_start_pro_heartbeat_returns_none_when_marker_missing`, `test_start_pro_heartbeat_returns_none_when_run_id_missing`, `test_start_pro_heartbeat_returns_none_when_token_missing` |
| §8 UTF-8 + newline-terminated | Engine stdout/stderr is UTF-8 and `\n`-terminated. | `ralph/display/*` (loguru sinks) | `test_pro_support_contract.py::test_section_8_loguru_logger_emits_utf8_newline_terminated_text` |
| §8 No bare structured JSON logs | Engine MUST NOT emit a single-line `{...}` or `[...]` as a log line. | (engine-wide; loguru uses text format) | `test_pro_support_contract.py::test_section_8_loguru_does_not_emit_bare_json_log_line`, `test_section_8_heartbeat_client_does_not_log_payload_as_bare_json` |
| §9 PROMPT.md ownership | Engine reads `PROMPT.md` (via `PROMPT_PATH` resolver) but never writes to it. | `ralph/pro_support/prompt.py` (read-only resolver) | `tests/test_pro_support_prompt.py::test_engine_does_not_modify_prompt_md_under_pro_mode` |
| §10 Config ownership | Engine never modifies `.agent/pipeline.toml`, `.agent/artifacts.toml`, or `.agent/mcp.toml`. | `ralph/config/loader.py:load_config` (read-only by contract) | `tests/test_pro_support_config.py::test_engine_does_not_reinterpret_pro_owned_config*` |
| §10 No drift in `PROMPT.md` literals | CI grep prevents hardcoded source-prompt construction outside the resolver. | `Makefile:verify-drift` | `make verify-drift` (CI-level proof) |
| §12A No engine-side WebSocket emission | Engine emits zero WebSocket events. | (absence; the engine has no WS code path) | (implicit; absent code path) |
| **n/a (user prompt bullet 1)** Late marker adoption | Engine adopts a marker that appears AFTER engine start. | `ralph/pro_support/watcher.py:ProMarkerWatcher` | `tests/test_pro_support_watcher.py`, `tests/test_run_loop_pro_integration.py::test_late_marker_adoption_starts_heartbeat_after_run` |
| **n/a (user prompt bullet 1)** Watcher is daemon-only | Watcher runs in a daemon thread; `stop()` returns promptly without joining. | `ralph/pro_support/watcher.py:ProMarkerWatcher._run_loop` | `tests/test_pro_support_watcher.py::test_watcher_stop_interrupts_sleep_within_50ms` |
| **§10 (Configuration Ownership, pending amendment)** Custom pipeline DI | `ProPipelineHooks` exposes 5 factory kwargs + 1 policy_bundle_override + 1 snapshot_registry + 1 recovery_sleep = 8 fields. | `ralph/pro_support/hooks.py:ProPipelineHooks` | `tests/test_pro_support_hooks.py` |
| **§8 (Log Pipeline Contract, pending amendment)** Pipeline state observability | `PipelineStateSnapshot` is a frozen, read-only view published on each reduce. | `ralph/pro_support/state_query.py:PipelineStateSnapshot` | `tests/test_pro_support_state_query.py` |
| **n/a (user prompt bullet 0)** Cross-repo handoff marker is present | `docs/agents/pro-contract.md` lists the three new engine capabilities by canonical name. | `docs/agents/pro-contract.md` (this file, forward-looking section) | `tests/test_pro_support_cross_repo_marker.py` |

## Test inventory (pro-support surface)

The full set of files that prove the engine's Pro contract:

| File | What it proves |
| --- | --- |
| `tests/test_pro_support_env.py` | The three env-var helpers accept injected mappings and default to `os.environ` only when omitted. |
| `tests/test_pro_support_workspace.py` | `RALPH_WORKSPACE` overrides the fallback. |
| `tests/test_pro_support_prompt.py` | `PROMPT_PATH` overrides `<workspace>/PROMPT.md`; engine never writes to `PROMPT.md` under Pro mode. |
| `tests/test_pro_support_marker.py` | Marker reader is read-only; heartbeat client does not write the marker. |
| `tests/test_pro_support_heartbeat.py` | Posts contract-shaped JSON, 401/404 hard-stop, transient errors continue, bounded timeouts. |
| `tests/test_pro_support_config.py` | Engine never modifies Pro-owned config files; no extra env vars introduced. |
| `tests/test_orchestrator_pro_prompt_resolution.py` | Orchestrator routes through the resolver when `workspace_scope` is provided. |
| `tests/test_run_loop_pro_integration.py` | Heartbeat is started/stopped in Pro mode; exit code preserved; marker gate works. |
| `tests/test_audit_mcp_timeout.py` | Bounded-subprocess audit covers `pro_support` by default. |
| `tests/test_pro_support_contract.py` | **Contract-level traceability assertions** — one test per named contract clause. |
| `tests/test_pro_support_watcher.py` | `ProMarkerWatcher` polls the marker and adopts on first appearance; daemon thread; never writes to the marker. |
| `tests/test_pro_support_hooks.py` | `ProPipelineHooks` exposes 5 factory kwargs + 1 policy_bundle_override + 1 snapshot_registry + 1 recovery_sleep; all wired into `run()`. |
| `tests/test_pro_support_state_query.py` | `PipelineStateSnapshot` is a frozen, read-only view of the live state; published on each reduce. |
| `tests/test_pro_support_cross_repo_marker.py` | The forward-looking engine-capability section in this file lists the three new engine surfaces by canonical name. |
| `tests/test_pro_support_end_to_end.py` | One black-box `run()` invocation exercises all 3 user-prompt bullets end-to-end: late-marker adoption via `ProMarkerWatcher`, snapshot observability via `SnapshotRegistry` + `PipelineStateSnapshot`, and custom-pipeline DI via `policy_bundle_override`. |

## Drift detection (CI-level)

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

## Coordination rule

When a task touches the Pro↔Ralph shared boundary, the contract
is the single source of truth. Update the contract first, then
update the engine code, then update this document, then update
the contract test in `tests/test_pro_support_contract.py` so the
new behaviour is pinned. A contract change that is not pinned by
a passing test is incomplete.

## Forward-looking engine capabilities pending contract amendment

The engine has three new surfaces that the upstream contract
has not yet been amended to formalise. Until the Pro
repository accepts the engine-side handoff patch
(`ralph-workflow/tmp/pro_contract_patch.md`), these
capabilities are engine-internal and NOT contractually
binding on the Pro product.

- `ProMarkerWatcher` — late marker adoption daemon thread;
  engine adopts the marker on first appearance. See
  `ralph/pro_support/watcher.py` and
  `tests/test_pro_support_watcher.py`. The `ProMarkerWatcher`
  is wired into `ralph/pipeline/run_loop.py:run` via
  `_start_pro_marker_watcher` and stops during
  `_cleanup_pipeline`.
- `ProPipelineHooks` — custom pipeline DI seam; 5 factory
  kwargs + 1 policy_bundle_override + 1 snapshot_registry
  + 1 recovery_sleep = 8 fields total. See
  `ralph/pro_support/hooks.py` and
  `tests/test_pro_support_hooks.py`. The `ProPipelineHooks`
  dataclass is consumed in `ralph/pipeline/run_loop.py:run`
  through the `pro_hooks` keyword argument and per-field
  short-circuits.
- `PipelineStateSnapshot` — read-only state observability;
  a frozen dataclass published on each reduce step. See
  `ralph/pro_support/state_query.py` and
  `tests/test_pro_support_state_query.py`. The
  `PipelineStateSnapshot` is published from
  `ralph/pipeline/run_loop.py:_run_inner_loop` after
  `state = step_result` and before the next iteration.

Until the upstream contract is amended, these engine
capabilities are documented in the engine-side
pro-contract.md, the handoff patch tmp/pro_contract_patch.md,
and the tracking log tmp/cross_repo_tracking.md only; they
are NOT contractually binding on the Pro product.

## See also

- `Ralph-Workflow-Pro/docs/product-spec/CONTRACT_RALPH_INTEGRATION.md`
  — the authoritative contract.
- `ralph-workflow/ralph/pro_support/` — the engine implementation.
- `ralph-workflow/docs/agents/verification.md` — verification
  policy (60 s combined test budget, drift detection).
- `ralph-workflow/docs/agents/testing-guide.md` — test
  conventions (no `time.sleep`, use `MemoryWorkspace` / fakes,
  inject clocks).
