# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## How to add an entry

> One line per change, grouped under Added / Changed / Fixed / Removed /
> Documentation. Link the commit SHA. Name the test that locks the
> behavior. Keep multi-paragraph prose out of the changelog — link to
> the commit, the PR, or the docs page that carries the full story.
> The [Unreleased] section is the only version section that exists
> today; historical `## [0.x.y]` sections are not synthesized (no git
> tags exist to backfill from, and inventing them would be fabrication).

### Conventions

- **Subject prefix**: `feat(...)`, `fix(...)`, `refactor(...)`,
  `docs(...)`, `chore(...)`, `test(...)` — matching the
  `ralph --generate-commit` subject style.
- **One line per change**: the description, the commit SHA, and the
  test file that pins it. Multi-paragraph prose is pruned to a
  one-line summary; deeper context lives in the commit message and the
  docs.
- **Group headers**: `### Added`, `### Changed`, `### Fixed`,
  `### Removed`, `### Documentation` (Keep a Changelog).
- **Test reference**: name the test module / file (e.g.
  `tests/test_verify_invariants.py`) so a reader can jump from the
  changelog to the regression that locks the behavior.

### Why only `[Unreleased]` today

`git tag --list` is empty, so there is no upstream anchor to backfill
historical `## [0.x.y]` sections from. Adding invented version sections
would violate the AGENTS.md fabrication guard. The `[Unreleased]`
section collects every change since the project started tracking the
changelog; a future release will rename it to the released version
and open a fresh `[Unreleased]`.

## [Unreleased]

### Added

- **feat(telemetry): attach a metadata-only agent-config snapshot, the project's policy-schema state, and the Ralph/Python versions to the Sentry session** — pinned by `tests/test_telemetry_sentry.py`. The `agent_config` context is set at the pipeline config-load chokepoint so it rides on every event including crashes; user-authored agent names, raw `cmd` strings, and flag values are dropped by `ralph/telemetry/_agent_config_payload.py` (transports stay closed-vocabulary, model IDs pass through, flags reduce to presence booleans). A `model` value that is a filesystem path or a credentialed endpoint URL — legitimate in ollama/llama.cpp/vLLM/LiteLLM setups — is rejected as `custom` rather than forwarded, since the `before_send` scrubber only rewrites the home/cwd prefix and would leave the rest of such a path (and any inline password) intact.

### Fixed

- **fix(telemetry): keep one random user identity across terminal-specific XDG environments and add queryable session timing plus safe logical-agent, pipeline-profile, and drain-class metrics** — pinned by `tests/test_telemetry_user_identity.py` and `tests/test_telemetry_sentry.py`.

- **fix(claude-interactive): preserve structured transcript envelopes so parallel tool results retain their tool identity and inputs, repeated session IDs stay suppressed, and result labels render once** — regression-guarded by `tests/test_claude_interactive_parser.py` and `tests/test_pty_transcript_tool_result.py`.

- **fix(policy): repair dead/stale research-citation URLs in bundled project-policy starters and add the policy-citation-linkcheck gate + offline citation-structure guard** -- pinned by `tests/project_policy/test_starters.py::test_starter_citations_are_structurally_valid`. Replaces 11 non-resolving URLs across 8 starter files (clean-code-policy.md, dependency-policy.md x2, linting-policy.md, memory-usage-policy.md x2, testing-policy.md, typechecking-policy.md x2, ux-policy.md, verification-policy.md) with primary sources verified to return HTTP 200 directly; adds `make policy-citation-linkcheck` (on-demand gate reusing `scripts/check_route_page_links.py`) and an offline pytest guard that asserts every citation block carries publisher/title/review-date fields, an https URL with a non-empty host, and an ISO `YYYY-MM-DD` review date.

- **fix(policy): repair corrupted completion-marker text in all 12 bundled project-policy starter files** — commit `b538b9dea`. The 'Ralph markers' section named a garbled token instead of `ralph-policy-complete`; regression-guarded by `tests/project_policy/test_starters.py`.

- **fix(recovery): route missing plan handoff back to entry phase from any non-planning phase** (development, planning_analysis, review, etc.) by dropping the `failed_route` guard in `ralph/pipeline/runner.py:_handle_inline_effect`. Locks behavior in `tests/pipeline/test_runner_missing_plan_handoff_recovery.py`.

- **fix(display): allowlist `tests/integration/test_status_bar_pty_e2e.py` in `tests/test_process_audit.py:TESTS_ALLOWLIST`** so the production Status Bar PTY regression test can use `subprocess.run` and `os.setsid` to drive the real-kernel-TTY reproduction (mirrors the `test_claude_interactive_interrupt_realtime.py` PTY pattern already in the allowlist). Locks behavior in `tests/test_process_audit.py::test_no_direct_subprocess_calls_in_tests`.

- **fix(display): wipe residual display-mode drift — replace stale mode="wide", narrow=False fixtures across 8 test helper sites with mode="default", drop 16 cosmetic test names still referencing 'wide mode' / 'compact mode' / 'medium mode', and add tests/display/test_single_mode_anti_drift_test_fixtures.py as the AST-level regression lock.**

- **fix(display): confirm the persistent bottom Status Bar visibly renders through the production entry point and lock the single-owner lifecycle** with the new end-to-end integration test `tests/integration/test_status_bar_runtime_visibility.py` covering (a) `ParallelDisplay` context-manager activation on a tty-like stream (`is_active` flips True inside, False after exit), (b) `update_status_bar` model propagation through to the composed `StatusBar.last_model`, (c) the Live region's captured buffer containing the workspace path, phase label, outer-dev iteration (`Dev 1/3`), and inner-analysis iteration (`Analysis 2/5`) when all are applicable, (d) phase-conditional omission when iteration fields are `None` (no `--` placeholder), (e) quiet-mode zero emission (no bytes written), and (f) non-tty console (force_terminal+plain `StringIO`) gate suppression so the `isatty()` conjunct is honored through the production entry point. Added the single-owner AST guard `tests/display/test_status_bar_single_owner.py` that asserts `ParallelDisplay` is the only class that constructs `StatusBar` (canonical site: `ralph/display/parallel_display.py:ParallelDisplay.__init__` at line 521) and the only class that calls `_status_bar.start()` / `_status_bar.stop()` (canonical sites: `ParallelDisplay.start` at line 1382, `ParallelDisplay.stop` at line 1390), preventing re-introduction of a second `StatusBar` constructor or a second start / stop site. The display subsystem remains locked to a single default mode (no compact / medium / wide tiers, no `force_mode` parameter, no `RALPH_FORCE_NARROW` env var); the existing `tests/display/test_single_mode_anti_drift.py` and `scripts/wt028-drift-check.sh` continue to guard the invariant. No production code changed; this commit is coverage-completion so the consolidated single default display mode is provable through the runtime entry point. **PTY regression lock:** the new `tests/integration/test_status_bar_pty_e2e.py` reproduces the production entry point inside `pty.openpty()` at a fixed 120x40 window size and asserts the same six contract points (workspace_root basename + canonical phase label visible in the captured stream, no `--` placeholder for omitted iteration fields, outer-dev iteration `Dev N/cap` visible when set, no Rich.Live alt-screen residue after the last phase banner, no Status Bar Live signature when the CLI runs with `stdout=subprocess.PIPE`, and the StatusBar real-TTY gate opens inside a real PTY), catching any regression that only manifests on a kernel TTY and would slip past the existing string-buffer-level pinning tests.

### Removed

- **refactor(display)!: remove dead force_mode= keyword argument and its NotImplementedError raise shim** from `make_display_context()` in `ralph/display/context.py`. Breaking change for any caller that explicitly passed `force_mode=` — those callers now raise `TypeError: unexpected keyword argument` instead of `NotImplementedError`. No production caller exists; locks regression in `tests/display/test_single_mode_anti_drift.py` (the two pinning tests removed).

- **refactor(display)!: remove orphaned public exports `RalphProgress` and `get_progress`** from `ralph/display/__init__.py` (and the orphaned `ralph/display/progress.py` + `ralph/display/_progress_singleton.py` + `ralph/display/progress_protocols.py` modules they lived in). Breaking change for any out-of-tree consumer that imported these names; zero production callers in this repo. `tests/test_display_progress.py` and its 5 dummy helper modules are removed in the same change.

- **chore(pipeline): consolidate redundant `_safe_push_status_bar` into the deduping `_push_status_bar_if_changed` push function** in `ralph/pipeline/run_loop.py` (module-private; no public API effect).

- **chore(cli): delete dead local `_status_text` helper** in `ralph/cli/commands/run.py` (module-private; no public API effect).

### Added

- **feat(mcp): ship indexed code-exploration substrate (Phases 0-4)** — commits `551e27517`, `1a1ba853c`, `5a576d4fa`, `fda81e2d2`, `b65ed3b89`. Deterministic SQLite+FTS5 store under `.agent/ralph-explore/` (gitignored, disposable) with stdlib `ast` Python structure extraction, Markdown heading/link extraction, and a SQLite edge-table code graph (no LLM, no embedding dependency). Adds three new MCP tools — `ralph_index_status`, `ralph_reindex`, `ralph_graph` (`neighbors` / `path` / `impact` / `hubs` / `tests`) — alongside indexed optional arguments on existing tools `grep_files` (`use_index`, `rank_by`, `return_evidence_ids`, `dedupe_by_symbol`, `include_graph_context`), `search_files` (`ranked`, `role`, `contains_symbol`, `changed_only`, `return_evidence_ids`), `read_file` (`evidence_id` / `span_id` / `symbol` / `context_lines` / `expected_content_hash`), `read_multiple_files` (mixed items + `per_item_max_bytes`), `list_directory` / `directory_tree` (`view`, `include_counts`, `include_symbols`, `changed_only`, `limit_children`, `use_index`), and `edit_file` (`expected_content_hash`, `target`, `match_strategy`, `reindex`, `impact_preview`); all gated by the shared `use_index` opt-in selector (`auto` / `never` / `always`) with a live-tool fallback. Deterministic before- and after-development/fix reindex lifecycle plus persistent `dirty_paths` queue mark-then-refresh semantics on `write_file`, `edit_file`, `append_file`, `move_file`, `copy_file`, and `delete_path`; Phase-0 tool-efficiency audit register captures every Ralph-owned MCP tool with `keep` / `add_argument` / `rework_internals` / `defer` outcomes and pins the Phase-5 deferral (NetworkX / Kuzu / Tree-sitter / vector / `ralph_explore` wrapper). Locks behavior in `tests/test_explore_pipeline.py`, `tests/test_explore_store.py`, `tests/test_explore_graph.py`, `tests/test_explore_grep_indexed.py`, `tests/test_explore_search_read_indexed.py`, `tests/test_explore_edit_indexed.py`, `tests/test_explore_handlers.py`, `tests/test_explore_ranking.py`, `tests/test_explore_structure.py`, `tests/test_explore_lifecycle.py`, `tests/test_explore_dirty_paths.py`, `tests/test_explore_bench.py`, `tests/test_explore_bench_gates.py` (evidence-recall = 1.0, returned-byte + tool-call budgets, no-op & small-edit reindex proportionality), `tests/test_explore_tool_descriptions.py` (tool ≤ 900 chars, argument ≤ 180 chars), `tests/test_explore_deferred_phases.py`, `tests/test_explore_audit_register.py`, `tests/test_explore_resource_lifecycle.py`, `tests/test_explore_mcp_timeout.py`, `tests/test_explore_legacy_shape_regressions.py`, and `tests/test_audit_parallelization_dormant.py`.

- **feat(display): add persistent bottom Status Bar showing working directory, active phase, and applicable outer development / inner analysis iterations during interactive runs** — commits `0ea756476`, `2d19483d1`, `666661755`, `ac8013e0a`, `c27b2a933`. Locks behavior in `tests/display/test_status_bar.py`.

### Changed

- **refactor(display): close wt-028-display — wire `scripts/wt028-drift-check.sh` into `make verify-drift` and add an end-to-end Status Bar wiring test that drives `_run_inner_loop` and asserts `_push_status_bar_if_changed` calls `display.update_status_bar`**. Locks behavior in `tests/display/test_status_bar.py`, `tests/display/test_single_mode_anti_drift.py`, `tests/display/test_di_invariants.py`, `tests/test_no_anti_drift_regression.py`, the new `tests/pipeline/test_run_loop_status_bar_wiring.py`, and the new `bash ./scripts/wt028-drift-check.sh` line in the `verify-drift` Makefile target.

- **refactor(display): consolidate to a single display mode so the persistent bottom Status Bar always shows working directory, active phase, and applicable outer development / inner analysis iterations for any applicable terminal width (>= 14 cols). At widths >= 40 cols the canonical ``Dev N/cap`` / ``Analysis N/cap`` iteration labels always render in full and only path middle-truncation / phase tail-truncation adapt to width; below 40 cols the implementation may degrade to compact (``D1/3`` / ``A2/5``) or minimal (``1/3`` / ``2/5``) forms to fit. Below 14 cols the iteration segments drop one at a time (outer_dev first, then inner_analysis, then both) so the bar never overflows the working area; phase and workspace path remain visible at every applicable width. Drops narrow / medium / wide modes, the legacy env-var override, `force_mode` parameter, three-tier path / label / limits tables, and consolidates the consolidated ``_DEFAULT_LIMITS`` constant into a single owner at `ralph/display/_mode_adaptive_limits.py`.** — commit `c07c708fc`. Locks behavior in `tests/display/test_single_mode_anti_drift.py`, `tests/display/test_status_bar.py`, `tests/display/test_mode_adaptiveness.py`, `tests/test_parallel_display.py`, `tests/test_display_context.py`, `tests/unit/display/test_display_context.py`, `tests/unit/display/test_mode.py`, and the broader `tests/` migration sweep that dropped `force_mode=` / `force_narrow` / `ctx.mode == compact|medium|wide` / `NARROW_THRESHOLD` / `MEDIUM_THRESHOLD`.

- **feat(watchdog): close wt-021 with consolidated Trustworthy Idle Watchdog acceptance-criteria test** — commits `2a4aaf9cd`, `4ddd85613`, `1bc186c8e`, `1c3d4a2de`. Locks behavior in `tests/agents/idle_watchdog/test_trustworthy_idle_watchdog_spec.py`.

- **fix(agy): align CLI default and live regression suite on `agy/Gemini 3.5 Flash (Medium)` and accept any canonical AGY model display name.** Locks behavior in `tests/test_agy_live_regression.py`, `tests/test_cli_smoke.py`, `tests/test_smoke_agy_end_to_end.py`.

- **feat(idle-watchdog): fast-fires NO_OUTPUT_AT_START at 30s default instead of 60s** in `ralph/timeout_defaults.py`. Locks behavior in `tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py`.

- **fix(idle-watchdog): preserves the NO_OUTPUT_AT_START baseline across LIFECYCLE frames** so a process-startup event cannot mask a hung agent, modifying `ralph/agents/idle_watchdog/idle_watchdog.py`. Locks behavior in `tests/agents/test_idle_watchdog_no_output_at_start_lifecycle.py`.

- **refactor(recovery): extracts UnavailabilityStore Protocol** in `ralph/recovery/agent_unavailability_tracker.py` as a runtime-checkable interface seam. Locks behavior in `tests/recovery/test_unavailability_tracker.py`.

- **feat(pipeline): emits structured WAITING / RESUMED logs** at INFO/DEBUG with recovery binding in `ralph/pipeline/run_loop.py` when all agents in a chain are unavailable. Locks behavior in `tests/pipeline/test_run_loop_waiting_state_logs.py` and `tests/recovery/test_all_agents_unavailable_never_crashes.py`.

- **fix(recovery): tightens FailureClassifier._SUBSCRIPTION_LIMIT_SUBSTRINGS with opencode / Claude Code / generic-API cases** in `ralph/recovery/failure_classifier.py`. Locks behavior in `tests/recovery/test_unavailability_reason.py` and `tests/recovery/test_out_of_credits_fast_fallover.py`.

- **refactor(artifact-submission): harden canonical artifact submission path and add make-verify audit.** Locks behavior in `tests/test_canonical_artifact_submit.py`, `tests/test_commit_plumbing_uses_canonical_submit.py`, `tests/test_smoke_plumbing_uses_canonical_submit.py`, and `tests/test_audit_artifact_submission_canonical_path.py`.

- **refactor(pipeline): route plumbing-direct-call fallback through `DefaultPipelineFactory` and add `pro_hooks` plumbing parameter.** Locks behavior in `tests/test_pipeline_factory_default.py` and `tests/integration/test_plumbing_shared_deps.py`.

- **refactor(pipeline): add `DefaultPipelineFactory` as single composition root for main pipeline and plumbing.** Locks behavior in `tests/test_pipeline_factory_default.py` and `tests/integration/test_cli_plumbing_uses_factory.py`.

- **refactor(idle-watchdog): consolidate watchdog logic, delete legacy root module, and enshrine the two main retry rules.** Locks behavior in `tests/recovery/test_two_main_retry_rules.py` and `tests/agents/idle_watchdog/test_dumb_kill_scenarios.py`.

- **feat(idle-watchdog): weight workspace file changes by class so only source-code changes count for `NO_OUTPUT_DEADLINE` by default. Behavior change:** operators who relied on log-file activity to defer the verdict must opt in via `[general] agent_workspace_change_weights`. Locks behavior in `tests/agents/test_dumb_kill_scenarios.py` and `tests/agents/idle_watchdog/`.

- **refactor(idle-watchdog): tighten `_is_no_progress_quiet` to defer `NO_PROGRESS_QUIET` when the corroborator reports any `alive_by` signal** — `NO_PROGRESS_QUIET` now fires only when the corroborator returns no `alive_by` signal AND no fresh channel evidence is present. Locks behavior in `tests/agents/idle_watchdog/test_dumb_kill_scenarios.py`.

- **feat(recovery): surface `child_alive` on `IdleWatchdogKilledError` to differentiate live-child from dead-child `NO_PROGRESS_QUIET`.** Locks behavior in `tests/recovery/test_two_main_retry_rules.py`.

- **fix(interrupt): rename `bridge_pids` kwarg to `bridge_pgids`**; legacy `bridge_pids=` accepted via deprecation shim. `INTERRUPT_EXIT_CODE` (130) unchanged.

- **Parallel plan execution is now delegated to the executing AI agent** via `dispatch_mode = "agent_subagents"`; Ralph-managed fan-out remains dormant and opt-in via `dispatch_mode = "ralph_fan_out"`. Locks behavior in `tests/test_continuation_template_parallel_guidance.py` and `tests/test_audit_parallelization_dormant.py`.

### Fixed

- **fix(agy): make live AGY end-to-end smoke green with deterministic alias, longer timeouts, and parser-classified events.** Locks behavior in `tests/test_agy_live_regression.py` and `tests/test_agy_harness_with_mock.py`.

- **fix(recovery): remove generic `rate_limited` marker from `OUT_OF_CREDITS` substring table.** Locks behavior in `tests/recovery/test_unavailability_reason.py::test_generic_throttling_does_not_classify_as_out_of_credits`.

- **fix(pipeline): bind pre-sleep wait-state DEBUG log with `recovery=True`** — `ralph/pipeline/run_loop.py:403` now uses `logger.bind(recovery=True).debug(...)` so the binding is present on every pre-sleep line (the AC-08 contract). Locks behavior in `tests/pipeline/test_run_loop_waiting_state_logs.py` and `tests/pipeline/test_run_loop_waiting_state_real_controller.py`.

- **fix(recovery): cap `_mark_agent_unavailable` return value at the reason's `max_backoff_ms`.** Locks behavior in `tests/recovery/test_out_of_credits_fast_fallover.py::test_controller_mark_agent_unavailable_caps_return_value_at_30_minutes`.

- **fix(recovery): strengthen wait-state survival test to 30 consecutive `handle()` calls with bounded-stability assertion.** Locks behavior in `tests/recovery/test_all_agents_unavailable_never_crashes.py`.

- **fix(recovery): add import-time AST invariant ensuring `RecoveryController` never exits from the all-agents-unavailable branch.** Locks behavior in `tests/recovery/test_two_state_invariant.py::test_never_exit_invariant_locked_at_import_under_python_O`.

- **fix(pipeline): replace brittle `last_error` text parser with structured `state.is_waiting_state` flag.** Locks behavior in `tests/pipeline/test_run_loop_waiting_state_real_controller.py`, `tests/pipeline/test_run_loop_waiting_state_logs.py`, and `tests/recovery/test_no_progress_quiet.py`.

- **refactor(recovery): inject `UnavailabilityStore` Protocol into `RecoveryControllerOptions` and expose public controller surface.** Locks behavior in `tests/pipeline/test_run_loop_waiting_state_real_controller.py` and `tests/recovery/test_unavailability_tracker.py`.

- **fix(agy): refresh upstream and local AGY source-of-truth and pin live AGY smoke with non-mock regression tests.** Locks behavior in `tests/test_agy_live_regression.py`, `tests/test_smoke_agy_end_to_end.py`, and `tests/test_check_mcp_cli_agy.py`.

- **fix(agy): align smoke harness fixtures, resolver tests, and documented examples with the real AGY v1.0.8 wire format.**

- **fix(agy): add `RALPH_AGY_BINARY` env override, deterministic mock AGY binary, and subprocess_e2e tests proving end-to-end output without a live account.**

- **fix(agy): move `RALPH_AGY_BINARY` override seam to the CLI surface and harden AGY smoke tests/docs.** Locks behavior in `tests/test_agy_harness_with_mock.py` and `tests/test_agy_plumbing_mock.py`.

- **fix(agy): drain the PTY master after child exit so live AGY buffered stdout is captured.** Locks behavior in `tests/test_agy_pty_invocation.py` and `tests/test_agy_live_regression.py`.

- **fix(interrupt): close sync entry-point seam on `handle_keyboard_interrupt` with injected `process_manager` and `poll_interval_s`.** Locks behavior in `tests/test_runner_interrupt.py` and `tests/pipeline/test_run_loop_interrupt.py`.

- **fix(interrupt): make asyncio first-SIGINT handler non-blocking by dispatching `begin_interrupt` and `run_early_escalation_poll` via `loop.run_in_executor` with a done callback.**

- **fix(interrupt): bound `_wait_for_list_active_empty` sleep to the remaining deadline, eliminating the fixed 0.01s spin.** Regression pinned by `test_dispatcher_block_wait_sleep_never_exceeds_remaining_deadline`.

- **fix(interrupt): make `install_signal_handlers` return an idempotent teardown callable and update `fan_out.py` to invoke it on every exit path.**

- **fix(interrupt): remove redundant per-pgid `kill_process_group` loop from `controller.force_interrupt`, relying on `ProcessManager.shutdown_all(0)` for SIGKILL.**

- **fix(commit_cleanup): surface untracked files in the cleanup diff and harden safety rules.** Locks behavior in `tests/test_commit_cleanup_diff.py` and `tests/test_phases_commit_cleanup.py`.

- **fix(invoke): wire subprocess reader `InactivityTimeoutOpts` with `session_resume_safe` and `resumable_session_id`.** Locks behavior in `tests/test_subprocess_reader_resume_safe.py` and `tests/test_pipeline_runner_opencode_resume_build_agent_recovery_plan_resumable_session.py`.

- **fix(effect_executor): make retry prompt action-aware so resume never inlines the original task.** Locks behavior in `tests/test_no_anti_drift_recovery_invariants.py`.

- **fix(interrupt): add black-box tests for second-SIGINT-during-first-SIGINT-executor-body and slow `begin_interrupt` escalation.**

- **fix(interrupt): add SYNC long-running-body test pin, remove dead `grace_period_s`, re-export dispatcher, extract `run_shutdown_block`, and pin import-time constants.** Locks behavior in `tests/test_runner_interrupt.py`, `tests/test_interrupt_constants.py`, and ADR-0001 D7/D8.

- **fix(interrupt): switch production `run_shutdown_block` to liveness-based `_wait_for_list_active_empty` instead of CPU polling.** Locks behavior in `tests/test_runner_interrupt.py` and `tests/test_interrupt_dispatcher.py`.

- **fix(agy): close the real CLI flow with relative `RALPH_AGY_BINARY` normalization and mock-backed parity.** Locks behavior in `tests/test_agy_live_regression.py` and `tests/test_cli_smoke.py`.

### Added

- **feat(idle-watchdog): add per-kind workspace event breakdown to `NO_OUTPUT_DEADLINE` fire diagnostic.**

- **feat(idle-watchdog): add `[general] agent_workspace_change_weights` configuration key for per-kind activity weighting.**

- **feat(idle-watchdog): make idle verdict activity-aware by considering stdout, `mcp_tool`, subagent, and workspace evidence channels.** Locks behavior in `tests/agents/test_idle_watchdog_3.py`.

- **feat(mcp): add `unsafe_mode` workflow flag to merge Ralph MCP into agent-native MCP configs instead of overwriting them.**

- **feat(pipeline): add structural restart-from-scratch fingerprint with import-time invariant guard.**

- **feat(pro_support): ship engine-side Pro integration contract implementation (`ralph.pro_support`) with heartbeat, marker watcher, snapshot, and hooks.** Locks behavior in `tests/test_pro_support_*.py`, `tests/test_run_loop_pro_integration.py`, `tests/test_orchestrator_pro_prompt_resolution.py`, and `tests/test_pro_support_contract.py`.

- **feat(mcp): reset session timing budget on every attempt boundary via `McpServer.reset_session_budget()` and bridge `notifications/reset_wrapup`.**

- **feat(skills): stop auto-updating user-global baseline skills on normal runs; only explicit `--force-init-skills` or `--init` overwrites them.**

- **feat(cli): auto-symlink baseline skill bundle into Codex, OpenCode, and AGY roots during `ralph --init`.**

- **feat(plan): add cheap-model shortcut fields and stricter plan-step validators.**

- **feat(plan): add optional `design` sub-section with typed SE sub-models.**

- **feat(agy): make AGY a first-class supported agent path with completion evidence, MCP tooling, and command flag ordering.**

- **feat(agy): add `ralph smoke-interactive-agy` canonical end-to-end verification command with dynamic `agy/<model>` resolver.**

- **feat(pi): add Pi.dev as a first-class supported agent path with parser, registry, command builder, and runtime resolver.** Locks behavior in `tests/agents/test_pi_dev_blackbox.py` and `tests/agents/parsers/test_pi_dev_wire_format_spec.py`.

- **feat(display): add dedicated `waiting_status_line` field on `PipelineSnapshot`.**

- **feat(display): render waiting status lines with kind-specific `[waiting]` tag and level.**

- **feat(display): include `CHILDREN_PERSIST_TOO_LONG` diagnostic in completion summary.**

- **`_dispatch_waiting_event` free function for testable subscriber seam** — extracted from the runner's closure so tests can inject a fake subscriber without a full pipeline.

- **feat(display): introduce `DisplayContext` as single source of truth for rendering.**

- **feat(display): detect terminal mode (`compact`/`wide`) and honor `RALPH_FORCE_NARROW`.**

- **display: semantic theme keys replace literal Rich style strings** across `phase_banner.py`, `cli/main.py`, and all display modules, using the Okabe-Ito palette as the single colour source.

- **display: add `RALPH_FORCE_NARROW` env knob** to force compact rendering on wide terminals.

- **test(policy): add custom policy workflow contract test** demonstrating fully renamed phase/routing/counter names.

- **feat(cli): add explanation sentences for `bypass_routes` and loopback caps in `ralph --explain-policy`.**

- **feat(cli): add ASCII workflow diagram to `ralph --explain-policy`.**

- **feat(parallel): implement same-workspace parallel workers v1** for disjoint `work_units`.

- **feat(parallel): add runner-boundary parallel preflight** rejecting invalid work-unit plans.

- **feat(parallel): add opt-in serialized post-fanout verification policy field.**

- **feat(parallel): add `PostFanoutVerificationEvent` handled by the reducer.**

- **feat(parallel): write `parallel_development_summary.json` after fan-out completes.**

- **feat(display): add section-rule headers to table/panel `emit_*` methods in non-compact mode.**

- **test(display): add black-box coverage for 11 previously untested `emit_*` methods.**

- **docs(architecture): add ADR-0001 interrupt architecture document.**

#### Upstream MCP server coverage

- **feat(idle-watchdog): extend `mcp_tool` activity channel to cover upstream MCP tool calls.** Locks behavior in `tests/mcp/test_mcp_activity_sink.py`.

### Removed

- **fix(interrupt): remove redundant `SignalBridge.pids` registration from `ralph.interrupt.asyncio_bridge`.**

- **Cloud reporting infrastructure removed.** The old cloud reporting package, API shim, config model, and TOML section have been removed in favour of the new `ralph.supervising` trackable instance model.

### Changed

- **refactor(policy): make `_compute_budget_state` fully policy-driven for any declared budget counter.**

- **feat(cli): render ASCII workflow diagram loopbacks unambiguously with counter annotations.**

- **refactor(pipeline): drive `FanOutDevelopmentEffect.run_post_fanout_verification` from the `parallel_execution.post_fanout_verification` policy field and default it to `False`.**

- **refactor(display): complete `DisplayContext` dependency injection across CLI runner and command modules.**

- **docs: update `getting-started.md`, `concepts.md`, `parallel-mode.md`, and `parallel-fan-out.md` to describe v1 same-workspace behavior.**

- **fix(jinja): correct `worker_developer.jinja` to include `shared/_unattended_mode.jinja` and tell workers they share the checkout.**

- **test(anti-drift): strengthen guard against free-function display imports.**

### Removed

- **chore(parallel): remove dead worktree-first parallel code paths**; only `ParallelExecutionMode.SAME_WORKSPACE` is supported.

- **chore(config): remove global `[parallel_execution]` block**; parallelization is now configured per phase under `[phases.<phase>.parallelization]`.

### Changed

- OpenCode runs no longer treat foreground process exit as terminal success — completion now requires either an explicit completion signal or the required phase artifact to be present in the workspace.

- Idle/timeout evaluation for OpenCode considers Ralph-tracked agent labels (label prefix `agent:`) via the injectable `LivenessProbe` in addition to OS-level descendants, so quiet parents with live subagent work are not killed prematurely.

- **Missing PROMPT.md error now actionable** — when `ralph` is run without a `PROMPT.md`, the preflight error message includes `Run \`ralph --init\` to scaffold PROMPT.md and project config files` so new users know exactly how to fix it.

- **ASCII banner shown on first run.** The Ralph ASCII banner is now printed above the 'Ralph first-run setup' panel on first invocation (and on `--regenerate-config`). It is suppressed on subsequent runs.

- **Self-teaching PROMPT.md template.** `ralph --init` now seeds `PROMPT.md` with a concrete example (Goal, Context, Acceptance criteria, Notes sections) instead of empty placeholders, so new users immediately see what a usable prompt looks like.

- **`ralph --diagnose` hint in Next steps.** The first-run welcome panel and the fallback next-steps list both now include a step recommending `ralph --diagnose` to validate the environment before the first pipeline run.

- **Install URLs for known missing agents.** When a known agent (`claude`, `opencode`) is reported as missing from PATH in the first-run panel, a one-line install hint with the official documentation URL is shown inline.

- **feat(cli): make verbose output the default** and map `--verbosity normal` to verbose; use `--quiet` / `-q` for minimal output.

- The live dashboard now renders `Plan`, `Analysis`, and `Decision Log` panels backed by `.agent/artifacts/plan.json` and the latest `_analysis_decision` artifacts, not just a prompt preview.

- Phase transitions emitted during a run are both printed above the live region and recorded into the dashboard's decision log.

- Pipelines now end with a `Pipeline Complete` (or `Pipeline Failed`) summary panel that echoes the plan, decision log, metrics, verification status, commit, PR URL, and open risks that the user saw live.

- **feat(logging): extend log format with `MILESTONE` level and `META`/`CONT` category prefix.**

- **feat(display): render completion summary with rule-delimited sections and badges.**

- **feat(display): enforce streaming block global-single invariant across workers.**

- **feat(display): manage raw-overflow reference in `PlainLogRenderer`.**

- **feat(display): enable long-content summary by default for blocks over 4000 display cells.**

- **feat(display): include fragment count and char length on streaming `*-end` lines.**

- **feat(display): add 1-based sequence numbers to streaming continue tags.**

- **`WARN META [progress]` for dropped ring-buffer lines** — debounced dropped-line notification now emitted at `WARN` level (not `INFO`) to match its documented severity.

- **Preflight guards unedited starter PROMPT.md** — when running `ralph` with a `PROMPT.md` that still contains the `<!-- ralph:starter-prompt ... -->` marker emitted by `ralph --init`, preflight fails fast with an error that tells the user to edit the file first, preventing an accidental pipeline run against the example task.

- **`--init` docs now use the real contract** — quick-start examples use bare `ralph --init`, compatibility labels remain deprecated/ignored, rerun output no longer prints a fake template selector.

- **chore(cli): remove hidden `isolation_mode` compatibility plumbing and `--no-isolation` path.**

### Added

- **feat(mcp): add built-in `visit_url` web fetcher MCP tool** gated by `WebVisit` capability and configured via `[web_visit]` in `mcp.toml`.

- **test(idle-watchdog): add AST audit `ralph.testing.audit_activity_aware_watchdog` to enforce activity-aware watchdog wiring invariants.** Locks behavior in `tests/test_audit_activity_aware_watchdog.py` and `tests/agents/test_idle_watchdog_3.py`.

- **`WebVisit` capability** (`web.visit` / `WebVisit`) added to `Capability` and `McpCapability` enums and wired into the capability mapping layer with the same alias and policy evaluation as `WebSearch`.

- **`WebVisitConfig`** pydantic model in `ralph.config.mcp_models` with `enabled`, `timeout_ms`, `max_bytes`, `user_agent`, `allow_private_networks`, and `extract_links` fields. Added as `web_visit` field on `McpConfig`.

- **`[web-visit]` extras group** in `pyproject.toml` (`readability-lxml>=0.8.1`, `selectolax>=0.3.21`).

- **Crawl4AI upstream MCP integration documented** in `docs/mcp/mcp-servers.md` — Ralph already supports upstream MCP servers; new section documents how to run Crawl4AI locally and wire it in for multi-page / JavaScript-rendered crawling. No new code; configuration only.

- **`ralph.agents.execution_state`** — provides `AgentExecutionState` (ACTIVE / WAITING_ON_CHILD / RESUMABLE_CONTINUE / TERMINAL_COMPLETE / FAILED) and per-transport `ExecutionStrategy` classes (`GenericExecutionStrategy`, `OpenCodeExecutionStrategy`) so liveness and completion semantics are isolated behind a transport-aware boundary.

- **`ralph.agents.completion_signals`** — provides `CompletionSignals` dataclass and `evaluate_completion(workspace, phase)` to determine whether an agent run produced the required phase artifact, making artifact submission the primary OpenCode success criterion.

- **`ralph.process.liveness`** — provides the `LivenessProbe` protocol, `DefaultLivenessProbe` (queries `ProcessManager` for active labels), and `FakeLivenessProbe` (injectable test fake) so unit tests can exercise multi-agent tree liveness logic without real subprocesses.

- **`OpenCodeResumableExitError`** in `ralph.agents.invoke` — raised when OpenCode exits with code 0 without producing the required phase artifact, allowing the runner to continue the same session instead of restarting from scratch.

- display: colorize LEVEL and CAT badges on TTY; plain text preserved under NO_COLOR/non-TTY.

- display: emit one-line legend inside the [run-start] block describing LEVEL/CAT/[tag] format.

- logging: register SUCCESS (25) and MILESTONE (35) loguru levels aligned with the transcript vocabulary.

- display: print the Rich completion panel at pipeline stop alongside the existing [run-end] lines.

- **feat(display): surface agent output as kind-tagged content/thinking/tool/tool-result lines** with raw-overflow pointers.

- `ralph.display.content_condenser` — predictable head+tail condensation for oversized content lines.

- `ralph.display.raw_overflow` — per-unit raw NDJSON overflow log writer.

- `ralph.display.completion_summary` — end-of-run panel renderer reused by the runner for both dashboard and lines modes.

- `ralph.display.panels.analysis` and `ralph.display.panels.decision_log` — new first-class dashboard regions.

- `ralph.display.artifact_reader` — tolerant readers for `plan.json` and `*_analysis_decision.json` used by the dashboard subscriber.

- `ParallelDisplay.emit_phase_transition` / `emit_analysis_result` — helpers that route transitions and decisions through both the live dashboard and the subscriber's decision log.

- `LiveDashboard.print_above` — serialised helper for printing banners above the live region without fighting the render thread.

- **feat(mcp): add strict startup validation for custom MCP servers configured via `mcp.toml`,** with `RALPH_MCP_STRICT=0` fallback.

- `ralph --diagnose` renders a `Custom MCP Servers` table (per-server status with tool counts) and an `Agent Transport Compatibility` table (Claude/Codex/OpenCode/AGY reachability per server).

- `ralph --check-mcp` runs the custom MCP startup validation and agent transport probe without starting the pipeline, returning exit code 0/1.

- **feat(display): group consecutive text/thinking lines into streaming blocks with summary metadata.**

- **feat(display): add default-on headline extraction for oversized content.**

- **feat(display): add `RingBuffer.consume_drop_delta()` and debounced `WARN META [progress]` drop notifications.**

- **`render_missing_plan_hint`** in `ralph.display.artifact_renderer` — emits an `INFO META [plan] (no plan artifact on disk)` line when planning completes without a `plan.json`, so the log always contains a plan entry.

- **feat(display): wire raw-overflow callback for malformed parser input.**

- **feat(display): emit empty-state placeholders for plan and activity sections at startup.**

- **`theme.log.*` styles** in `RALPH_THEME` — `theme.log.info`, `theme.log.success`, `theme.log.warn`, `theme.log.error`, and `theme.log.milestone` give Rich TTY output visually distinct level badges while plain ANSI-free output continues to emit literal level strings unchanged.

- **`show_phase_start_from_state(state, phase)`** in `ralph.display.phase_banner` and re-exported from `ralph.display` — convenience helper that extracts iteration, reviewer pass, and analysis iteration counters from any state-like object and delegates to `show_phase_start`.

- **feat(display): emit `[run-start]` orientation line at pipeline start.**

- **feat(display): report timing and activity counters in `[phase-close]` lines.**

- **feat(display): emit `[run-end]` MILESTONE block with total elapsed and aggregate counters.**

- **feat(display): suppress identical consecutive streaming fragments by default.**

### Migration

- Users relying on the previously silent default behavior should now pass `--quiet` (or `-q`) explicitly.

- Users currently passing `--verbosity normal` will see verbose output; they will need to switch to `--quiet` to restore the minimal variant.

- Log parsers that assumed a fixed level set `{INFO,WARN,ERROR,SUCCESS}` must now also accept `MILESTONE` on phase-transition rows.

- Log parsers that matched `[content-continue]` or `[thinking-continue]` exactly must update to match `[content-continue#N]` / `[thinking-continue#N]` (where N is a 1-based integer).

- Tooling that expected dropped-buffer notifications at `INFO` level must update to `WARN`.

### Fixed

- **fix(idle-watchdog): fire OS-descendant-only child evidence at `max_waiting_on_child_no_progress_seconds` (default 600s).**

- **fix(policy): reject commit phases that track a budget counter without matching `post_commit_routes`.**

- **fix(opencode): eliminate false-positive `OpenCodeResumableExitError` by waiting for late completion artifacts and child agents before declaring resumable failure.**

- `-D` / `--developer-iters` and `-R` / `--reviewer-reviews` CLI flags now correctly control the number of dev/review cycles. Previously, the pipeline ran exactly one dev cycle and one review pass regardless of the flag values. The `developer_iters` setting controls dev cycles; `reviewer_reviews` controls review cycles.

- `-R=0` now skips the review phase entirely instead of running one forced review pass.

- Analysis loopbacks from `development_analysis` no longer consume a development budget slot. Loopbacks are retries of the same iteration, not new iterations.

- `make typecheck` now runs mypy through `uv run python -m mypy` so it uses the project's virtualenv instead of a system-wide mypy that cannot see project dependencies.

### Removed

- **chore(parallel): remove `commit_sha` field from worker events/state/snapshot** (worktree-era relic).

- `max_dev_continuations` config field (previously declared but never used).

- `_legacy_handle_agent_success` reducer path (unreachable in production — policy is always loaded).

- Unused `developer_iters_option` / `reviewer_reviews_option` decorator stubs in `cli/options.py`.

### Documentation

- **docs(mcp): document the indexed-exploration substrate (Phases 0-4) for `ralph_index_status`, `ralph_reindex`, `ralph_graph`, and the indexed optional arguments on `grep_files` / `search_files` / `read_file` / `read_multiple_files` / `list_directory` / `directory_tree` / `edit_file`, including the `use_index` auto/never/always fallback contract and the deterministic before-/after-development/fix reindex lifecycle with `dirty_paths` queue mark-then-refresh semantics on `write_file` / `edit_file` / `append_file` / `move_file` / `copy_file` / `delete_path`.** No fabricated version section introduced; full coverage stays under `[Unreleased]` per the AGENTS.md fabrication guard. Pins documentation synchronization in `tests/test_documentation_command_sync.py`, `tests/test_repo_root_docs_sync.py`, and `tests/test_package_markdown_docs_sync_package_mcp_docs.py`.

- **docs(consolidation): close wt-026 by collapsing overlapping guides and removing process cruft.** The doc surface is reduced to one canonical home per topic; legacy duplicates are deleted (root `CODE_STYLE.md`, `docs/architecture/overview.md`, `docs/plans/README.md`, `docs/tooling/{README.md,python-tooling.md}`, `ralph-workflow/docs/mcp/{README.md,mcp-servers.md,web-search.md,web-visit.md}`, the `X9/` stray directory, the badge-farming `assets/built-with-ralph-loop.svg`, the developer-only `run_ralph.sh` / `run_verify_proof.py` scripts, and the root `checkpoint.json` ephemeral state); the root `CONTRIBUTING.md`, `START_HERE.md`, `README.md`, `PROMPT.md`, the `docs/` map, the `ralph-workflow/docs/README.md` router, the package `README.md`, the Sphinx `index.rst` (rewritten as a curated 19-entry toctree), and the `ralph-workflow/CONTRIBUTING.md` are all reduced to their canonical role. Behavior change: every cross-reference in surviving docs resolves; `make verify` continues to pass; the fabrication guard at Level 1 stays clean on every touched file. Locks the canonical-home mapping in `tests/test_documentation_command_sync.py`, `tests/test_repo_root_docs_sync.py`, `tests/test_package_markdown_docs_sync_package_mcp_docs.py`, `tests/test_repo_root_operational_docs_sync.py`, and the surviving per-page Sphinx structure assertions.

- **docs(pi): close documentation gaps by enumerating `pi` in configuration, concepts, parallel-mode, and advanced-mcp docs.** Locks behavior in `tests/agents/invoke/test_pi_command_builder_and_runtime_resolver.py` and `tests/agents/parsers/test_pi_dev_wire_format_spec.py`.

### Migration Notes

- **docs(migration): users resuming from a prior buggy checkpoint must delete `.agent/checkpoint.json` before re-running `ralph -D N -R M` from scratch.**
