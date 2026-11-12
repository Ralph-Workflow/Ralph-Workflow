from __future__ import annotations

import ast
import multiprocessing
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.executor.process import TIMEOUT_EXIT_CODE
from ralph.process._spawn_env import sanitize_process_environment
from ralph.process.manager import ProcessManager, ProcessManagerPolicy, SpawnOptions
from ralph.verify_timeout import (
    DEFAULT_SUITE_TIMEOUT_SECONDS,
    DEFAULT_TEST_TIMEOUT_SECONDS,
    TEST_TIMEOUT_ENV,
    build_timeout_env,
    timeout_seconds_from_env,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from typing import Protocol

    class ShardProcess(Protocol):
        def poll(self) -> int | None: ...

        def communicate(
            self,
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes | None, bytes | None]: ...

        def terminate(self, grace_period_s: float | None = None) -> None: ...

        def cleanup_orphans(self) -> None: ...

    class ShardSpawner(Protocol):
        def __call__(
            self,
            command: Sequence[str],
            *,
            cwd: Path,
            env: Mapping[str, str],
        ) -> ShardProcess: ...


_PYTEST_SHARD_PROCESS_MANAGER = ProcessManager(
    policy=ProcessManagerPolicy(log_events=False, enable_zombie_reaper=False)
)
_DEFAULT_PYTEST_WORKERS = "auto"
_HETEROGENEOUS_CORE_HOST_MAX_CORES = 12


def _max_pytest_workers_for_host() -> int:
    return 8


_MAX_PYTEST_WORKERS = _max_pytest_workers_for_host()
_PERFORMANCE_CORE_PYTEST_WORKER_CAP = 10
_MINIMUM_MULTI_SHARD_CORES = 2
_DEFAULT_XDIST_WORKERS_PER_SHARD = "0"
_MAX_XDIST_WORKERS_PER_SHARD = 4

EXCLUSIVE_SUBPROCESS_E2E_FILES: tuple[str, ...] = (
    "tests/agents/test_terminal_state_restored_on_exit.py",
)

REQUIRED_AUTO_INTEGRATE_E2E_FILES: tuple[str, ...] = (
    "tests/test_auto_integrate_end_to_end.py",
    "tests/test_auto_integrate_recovery_git_state_reflog.py",
    "tests/test_integration_obstruction_real_git.py",
    "tests/test_pending_merge_commit_recovery.py",
    "tests/test_pending_merge_repair_real_git.py",
    "tests/test_retained_resolution_handoff.py",
    "tests/test_retained_resolution_dispatch.py",
    "tests/integration/test_retained_integration_acceptance.py",
    "tests/test_auto_integrate_remote_sync_fast_forward_contract.py",
    "tests/test_auto_integrate_acceptance_scenarios_e2e.py",
    "tests/test_auto_rebase_idempotence_e2e.py",
    "tests/test_backup_ref_operation_scope.py",
    "tests/test_tool_git_read_path_validation.py",
    "tests/test_commit_review_invariants.py",
    "tests/test_commit_review_verify_gate.py",
    "tests/integration/test_pipeline_commit_review_to_commit_e2e.py",
    "tests/test_phases_commit_review.py",
    "tests/test_commit_review_real_diff_proofs.py",
    "tests/integration/test_commit_review_rock_solid.py",
    "tests/integration/test_pipeline_final_commit_review_to_final_commit_e2e.py",
    "tests/test_git_commit_review.py",
    "tests/test_explore_crash_safety.py",
    "tests/test_explore_concurrency.py",
    "tests/test_explore_cold_start_lifecycle.py",
    "tests/test_explore_perf_regression_gate.py",
    "tests/test_completed_rebase_landing_blackbox.py",
    "tests/test_skills_install_auto_commit.py",
    "tests/project_policy/test_policy_auto_commit.py",
    "tests/test_scoped_auto_commit.py",
    "tests/test_config_auto_commit.py",
    "tests/test_config_bootstrap_git_isolation.py",
    "tests/test_deterministic_writers_clean_tree.py",
    "tests/config/test_agents_file_immutable.py",
    "tests/config/test_init_agents_autowire.py",
    "tests/display/test_generated_scene_catalog.py",
    "tests/display/test_generated_scene_renderer.py",
    "tests/display/test_generated_scene_salience.py",
    "tests/test_integration_diagnostic_worktree_isolation.py",
    "tests/agents/invoke/test_real_pty_completion_no_terminal_lost.py",
    "tests/test_audit_opts_key_drift.py",
    "tests/test_audit_kwargs_forwarding.py",
    "tests/test_authority_audit_scenario_1_2_worktree_target.py",
    "tests/mcp/test_reserved_socket_held_across_respawn.py",
    "tests/mcp/test_mcp_endpoint_never_refuses.py",
    "tests/mcp/test_mcp_slow_startup_served_from_backlog.py",
    "tests/mcp/test_respawn_no_discovery.py",
    "tests/mcp/test_mcp_server_startup_stages_observable.py",
    "tests/mcp/test_mcp_server_last_stage_when_stage_file_missing.py",
    "tests/mcp/test_mcp_stage_attribution_real_child.py",
    "tests/mcp/test_restart_aware_bridge_failed_restart_fn.py",
    "tests/mcp/test_restart_aware_bridge_policy_cooldown.py",
    "tests/mcp/test_runtime_respawn_precheck.py",
    "tests/mcp/test_discovery_aggregate_budget.py",
    "tests/mcp/test_start_stage_tracker_fresh_spawn.py",
    "tests/mcp/test_preflight_error_carries_stage_and_elapsed.py",
    "tests/mcp/test_runtime_blocked_registry_built_surfaces_typed_error.py",
    "tests/mcp/test_effective_config_fingerprint.py",
    "tests/test_display_lifecycle_label_taxonomy.py",
    "tests/test_unreadable_record_entry_points.py",
    "tests/mcp/test_cli_child_banner_visible_without_unbuffered_env.py",
    "tests/mcp/test_whole_startup_budget_production.py",
    "tests/mcp/test_bounded_catalog_publish.py",
    "tests/mcp/test_inherited_fd_invalid_fails_closed.py",
    "tests/mcp/test_wire_ledger_fail_closed.py",
    "tests/mcp/test_probe_overrun_self_measure.py",
    "tests/mcp/test_bridge_attempt_vs_budget.py",
    "tests/mcp/test_bridge_make_before_break.py",
    "tests/mcp/test_bridge_e1_e2_edge_cases.py",
    "tests/mcp/test_bridge_e3_e4_edge_cases.py",
    "tests/mcp/test_bridge_e5_e6_e7_edge_cases.py",
    "tests/mcp/test_e16_store_interleave_real_children.py",
    "tests/mcp/test_e17_state_continuity.py",
    "tests/mcp/test_e18_old_session_id_accepted.py",
    "tests/mcp/test_e24_fresh_stage_id.py",
    "tests/mcp/test_e25_candidate_survives_agent_tmp_deletion.py",
    "tests/mcp/test_e26_broken_workspace_package.py",
    "tests/mcp/test_e33_listening_fd_real_child.py",
    "tests/mcp/test_e3x_transport_gap_typed_retry.py",
    "tests/mcp/test_e42_two_supervisors_one_replacement.py",
    "tests/mcp/test_e46_parent_sigkill_no_orphans.py",
    "tests/mcp/test_e64_import_guard_execution.py",
    "tests/mcp/test_e72_footprint_real_child.py",
    "tests/mcp/test_handover_inflight_real_child.py",
    "tests/mcp/test_replacement_shutdown_signals_real_child.py",
    "tests/mcp/test_root_cause_real_child_bridge.py",
    "tests/mcp/test_s1_independent_counters_contract.py",
    "tests/mcp/test_single_acceptor_instrumented.py",
    "tests/mcp/test_spawn_early_failure_typed.py",
    "tests/test_exec_smoke_subprocess_e2e.py",
    "tests/test_exec_job_shutdown_subprocess_e2e.py",
    "tests/test_skills_upstream_transport.py",
)

SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES: tuple[str, ...] = (
    "tests/conftest.py",
    "tests/pipeline/test_reducer_lock_retry.py",
    "tests/test_auto_integrate_gates.py",
    "tests/test_bootstrap_auto_seed_git_exclude.py",
    "tests/test_bootstrap_auto_seed_gitignore.py",
    "tests/test_commit_review_diff.py",
    "tests/test_commit_review_reject_citation_gate.py",
    "tests/test_commit_review_tripwires.py",
    "tests/test_commit_detached_head_recovery.py",
    "tests/test_commit_executor.py",
    "tests/test_git_proposal_seal.py",
    "tests/integration/test_commit_review_gate_e2e.py",
    "tests/test_cycle_baseline_diff_cycle_baseline_diff.py",
    "tests/test_cycle_baseline_diff_runner_cycle_start_uses_force_true.py",
    "tests/test_cycle_baseline_diff_write_cycle_baseline_force_param.py",
    "tests/test_git_command_queue.py",
    "tests/test_git_wrapper.py",
    "tests/test_integration_diagnostic_livelock.py",
    "tests/test_pipeline_runner_execute_commit_effect.py",
    "tests/test_prompt_materialize_2.py",
    "tests/test_prompt_materialize_3.py",
    "tests/test_worktree_lease.py",
)
_VERIFICATION_MARK_EXPRESSION = "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e"
NON_SELECTABLE_REQUIRED_REGISTRY_FILES: frozenset[str] = frozenset({"tests/conftest.py"})
_SUBPROCESS_E2E_MARK_EXPRESSION = (
    "subprocess_e2e and not smoke and not live_agy and not verify_budget_real_time"
)
_FOCUSED_PROFILE_MARK_EXPRESSION = "not subprocess_e2e and not smoke"
_UNIT_PROFILE = "unit"
_INTEGRATION_PROFILE = "integration"
_FAST_PROFILE = "fast"
FAST_PROFILE_TEST_FILES: tuple[str, ...] = (
    "tests/test_makefile_verification_workflow.py",
    "tests/test_test_suites.py",
    "tests/test_test_suites_orchestration.py",
)
_FOCUSED_PROFILES = frozenset((_UNIT_PROFILE, _INTEGRATION_PROFILE, _FAST_PROFILE, "durations"))
_DURATIONS_PROFILE = "durations"
_DURATIONS_PER_FILE_TIMEOUT_SECONDS = 30.0
_PROFILE_OPTION_ARGUMENT_COUNT = 2
_SHARD_POLL_INTERVAL_SECONDS = 0.01
_SHARD_TERMINATION_DRAIN_SECONDS = 1.0
_REQUIRED_E2E_WEIGHT_MULTIPLIER = 1
_PYTEST_DURATIONS_REPORT: int = 10


_REQUIRED_E2E_SHARD_COUNT = 10


def _required_e2e_shard_count() -> int:
    available_cores = os.cpu_count() or 1
    return min(_REQUIRED_E2E_SHARD_COUNT, available_cores)


def _exclusive_subprocess_e2e_xdist_workers() -> str:
    return "4"


_EXCLUSIVE_SUBPROCESS_E2E_SHARD_XDIST_WORKERS = _exclusive_subprocess_e2e_xdist_workers()
_PARAMETRIZE_CASES_ARGUMENT_INDEX = 1
_WEIGHT_SECONDS_PER_UNIT: Final = 1.0 / 17.0
_BASE_SECONDS_PER_TEST: Final = 0.005
_DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES: Mapping[str, int] = {
    "tests/agents/invoke/test_invoke_monitor_wiring.py": 13,
    "tests/agents/invoke/test_runtime_resolver_endpoint_delivery.py": 11,
    "tests/agents/test_invoke_timeout_integration.py": 28,
    "tests/agents/test_terminal_state_restored_on_exit.py": 270,
    "tests/agents/test_workspace_event_backpressure.py": 12,
    "tests/config/test_agents_file_immutable.py": 61,
    "tests/config/test_agents_normal_run_immutable.py": 13,
    "tests/config/test_agents_toml_fallback.py": 22,
    "tests/config/test_check_config_reports_every_unreadable_file.py": 24,
    "tests/config/test_init_agents_autowire.py": 10,
    "tests/config/test_regenerate_agents_safe.py": 9,
    "tests/config/test_retention_max_age_config.py": 14,
    "tests/display/test_generated_scene_catalog.py": 13,
    "tests/display/test_generated_scene_renderer.py": 31,
    "tests/display/test_generated_scene_salience.py": 9,
    "tests/integration/test_commit_review_baseline_e2e.py": 16,
    "tests/integration/test_commit_review_gate_e2e.py": 84,
    "tests/integration/test_commit_review_gate_failure_e2e.py": 16,
    "tests/integration/test_legacy_restart_acceptance.py": 21,
    "tests/integration/test_lifecycle_restart_acceptance.py": 71,
    "tests/integration/test_mcp_exec_result_contract.py": 21,
    "tests/integration/test_pipeline_commit_review_to_commit_e2e.py": 103,
    "tests/integration/test_retained_integration_acceptance.py": 92,
    "tests/integration/test_runner_default_display.py": 24,
    "tests/integration/test_runner_fanout_wiring.py": 10,
    "tests/integration/test_single_agent_e2e.py": 23,
    "tests/integration/test_transcript_end_to_end.py": 57,
    "tests/integration/test_validate_custom_mcp_e2e.py": 16,
    "tests/mcp/test_boot_allowance_lifecycle.py": 9,
    "tests/mcp/test_bounded_catalog_publish.py": 56,
    "tests/mcp/test_bridge_e1_e2_edge_cases.py": 23,
    "tests/mcp/test_bridge_make_before_break.py": 79,
    "tests/mcp/test_cli_child_banner_visible_without_unbuffered_env.py": 29,
    "tests/mcp/test_codex_config_synthesis.py": 11,
    "tests/mcp/test_e17_state_continuity.py": 43,
    "tests/mcp/test_e18_old_session_id_accepted.py": 19,
    "tests/mcp/test_e3x_transport_gap_typed_retry.py": 234,
    "tests/mcp/test_e42_two_supervisors_one_replacement.py": 47,
    "tests/mcp/test_e46_parent_sigkill_no_orphans.py": 62,
    "tests/mcp/test_e54_bridge_zombie.py": 13,
    "tests/mcp/test_e64_import_guard_execution.py": 96,
    "tests/mcp/test_e72_footprint_real_child.py": 63,
    "tests/mcp/test_handover_inflight_real_child.py": 103,
    "tests/mcp/test_inherited_fd_invalid_fails_closed.py": 58,
    "tests/mcp/test_mcp_activity_relay_sequence_anomaly.py": 11,
    "tests/mcp/test_mcp_endpoint_never_refuses.py": 78,
    "tests/mcp/test_mcp_slow_startup_served_from_backlog.py": 61,
    "tests/mcp/test_mcp_stage_attribution_real_child.py": 26,
    "tests/mcp/test_replacement_shutdown_signals_real_child.py": 89,
    "tests/mcp/test_respawn_no_discovery.py": 199,
    "tests/mcp/test_root_cause_real_child_bridge.py": 291,
    "tests/mcp/test_runtime_blocked_registry_built_surfaces_typed_error.py": 25,
    "tests/mcp/test_runtime_blocked_workspace_read_surfaces_typed_error.py": 10,
    "tests/mcp/test_single_acceptor_instrumented.py": 48,
    "tests/mcp/test_whole_startup_budget_production.py": 120,
    "tests/pipeline/test_auto_integrate_agent_failure.py": 220,
    "tests/pipeline/test_commit_retry_real_route.py": 18,
    "tests/pipeline/test_conflict_resolution_ledger.py": 9,
    "tests/pipeline/test_conflict_resolution_unresolved_typed.py": 9,
    "tests/pipeline/test_continuation_retained_reason_gate.py": 22,
    "tests/pipeline/test_continuation_retry_gate.py": 79,
    "tests/pipeline/test_continuation_retry_gate_blackbox.py": 136,
    "tests/pipeline/test_corrupt_agents_toml_repro.py": 11,
    "tests/pipeline/test_development_phase_missing_plan_handoff_status_bar_visible.py": 8,
    "tests/pipeline/test_paused_rebase_config_recovery_blackbox.py": 180,
    "tests/pipeline/test_planning_commit_recovery.py": 11,
    "tests/pipeline/test_planning_requires_target_sync.py": 40,
    "tests/pipeline/test_reducer_lock_retry.py": 21,
    "tests/pipeline/test_run_loop_resume_prefers_available_agent.py": 29,
    "tests/pipeline/test_terminal_mcp_error_per_entry_path.py": 21,
    "tests/project_policy/test_completed_upgrades.py": 11,
    "tests/project_policy/test_fresh_init_v6.py": 12,
    "tests/project_policy/test_pipeline_driver.py": 16,
    "tests/project_policy/test_policy_auto_commit.py": 26,
    "tests/project_policy/test_policy_handback_channel_ledger.py": 29,
    "tests/project_policy/test_policy_handback_ready_route.py": 23,
    "tests/project_policy/test_policy_handback_to_pipeline.py": 20,
    "tests/project_policy/test_policy_handback_working_tree.py": 92,
    "tests/project_policy/test_policy_never_blocks_the_run.py": 34,
    "tests/project_policy/test_preflight.py": 11,
    "tests/project_policy/test_starter_enforcement_prose.py": 9,
    "tests/project_policy/test_v4_corpus_authentic.py": 11,
    "tests/project_policy/test_validator.py": 20,
    "tests/recovery/test_agent_failure_charge_once.py": 31,
    "tests/recovery/test_agent_failure_route_facts.py": 18,
    "tests/recovery/test_agent_failure_scenarios.py": 79,
    "tests/recovery/test_ledger_migration.py": 34,
    "tests/recovery/test_no_progress_quiet.py": 9,
    "tests/recovery/test_run_loop_real_resume_restart.py": 16,
    "tests/recovery/test_runner_offline_pause_resume.py": 22,
    "tests/recovery/test_runner_recovery_end_to_end.py": 44,
    "tests/recovery/test_single_agent_broken_chain_failfast.py": 60,
    "tests/test_agent_chain.py": 11,
    "tests/test_agents_invoke.py": 31,
    "tests/test_analysis_prompt_payload_contract_retry_hint_guard_in_templates.py": 12,
    "tests/test_audit_opts_key_drift.py": 280,
    "tests/test_audit_terminal_escape_containment.py": 29,
    "tests/test_audit_test_policy.py": 22,
    "tests/test_authority_audit_scenario_3_4_9_restart_retry.py": 43,
    "tests/test_auto_integrate_acceptance_scenarios_e2e.py": 210,
    "tests/test_auto_integrate_boundary_cleanliness.py": 48,
    "tests/test_auto_integrate_boundary_refresh.py": 177,
    "tests/test_auto_integrate_catchup_remote_coordination.py": 44,
    "tests/test_auto_integrate_conflict_budget.py": 180,
    "tests/test_auto_integrate_drift_recovery.py": 13,
    "tests/test_auto_integrate_end_to_end.py": 40,
    "tests/test_auto_integrate_fail_closed_e2e.py": 13,
    "tests/test_auto_integrate_gates.py": 208,
    "tests/test_auto_integrate_local_fleet_target_e2e.py": 55,
    "tests/test_auto_integrate_race.py": 140,
    "tests/test_auto_integrate_real_agent_resolution_e2e.py": 100,
    "tests/test_auto_integrate_record_loader_lease.py": 14,
    "tests/test_auto_integrate_recovery_git_state_reflog.py": 27,
    "tests/test_auto_integrate_remote_refresh.py": 300,
    "tests/test_auto_integrate_remote_sync_fast_forward_contract.py": 252,
    "tests/test_auto_integrate_remote_sync_pull.py": 46,
    "tests/test_auto_integrate_remote_sync_push.py": 25,
    "tests/test_auto_integrate_resolution.py": 165,
    "tests/test_auto_integrate_resolving_rebase_record.py": 13,
    "tests/test_auto_integrate_retained_recovery_seam.py": 68,
    "tests/test_auto_integrate_skip_reasons.py": 13,
    "tests/test_auto_rebase_idempotence_e2e.py": 60,
    "tests/test_backup_ref_operation_scope.py": 44,
    "tests/test_bootstrap_auto_seed_gitignore.py": 17,
    "tests/test_branch_relation_matrix.py": 78,
    "tests/test_cli_commands_1.py": 33,
    "tests/test_cli_commands_init.py": 14,
    "tests/test_cli_commands_run_skill_sync.py": 15,
    "tests/test_cli_commit_command.py": 9,
    "tests/test_cli_diagnose_path.py": 46,
    "tests/test_cli_long_mode_semantics.py": 14,
    "tests/test_cli_quick_mode_semantics.py": 11,
    "tests/test_cli_removed_review_flags.py": 9,
    "tests/test_cli_run_skill_sync_warnings.py": 10,
    "tests/test_cli_subcommand_early_return_proof.py": 11,
    "tests/test_commit_detached_head_recovery.py": 217,
    "tests/test_commit_executor.py": 169,
    "tests/test_commit_review_diff.py": 31,
    "tests/test_commit_review_invariants.py": 126,
    "tests/test_commit_review_reject_citation_gate.py": 36,
    "tests/test_commit_review_tripwires.py": 44,
    "tests/test_commit_review_verify_gate.py": 149,
    "tests/test_completion_invocation_exit_logging.py": 14,
    "tests/test_config_auto_commit.py": 45,
    "tests/test_config_bootstrap.py": 61,
    "tests/test_config_bootstrap_git_isolation.py": 49,
    "tests/test_conflict_resolution_attempt_attribution.py": 16,
    "tests/test_conflict_resolution_futile_loop.py": 23,
    "tests/test_conflict_resolution_gitlink_escalation.py": 210,
    "tests/test_conflict_resolution_phase_parity.py": 12,
    "tests/test_conflict_resolution_pipeline.py": 83,
    "tests/test_conflict_resolution_pipeline_outcome.py": 12,
    "tests/test_cursor_runtime_auth_preservation.py": 10,
    "tests/test_deterministic_writers_clean_tree.py": 27,
    "tests/test_developer_prompt_subagent_guidance.py": 9,
    "tests/test_display_single_source_formatters.py": 15,
    "tests/test_exec_job_integration.py": 34,
    "tests/test_exec_job_shutdown_subprocess_e2e.py": 207,
    "tests/test_exec_jobs.py": 188,
    "tests/test_exec_jobs_finish_regressions.py": 54,
    "tests/test_exec_jobs_lifecycle.py": 12,
    "tests/test_exec_liveness_scenarios.py": 16,
    "tests/test_exec_smoke_subprocess_e2e.py": 79,
    "tests/test_executor_process.py": 12,
    "tests/test_explore_bench_rss_isolation.py": 42,
    "tests/test_explore_cold_start_lifecycle.py": 20,
    "tests/test_explore_concurrency.py": 12,
    "tests/test_explore_crash_safety.py": 35,
    "tests/test_explore_fault_matrix.py": 18,
    "tests/test_explore_fault_matrix_full.py": 11,
    "tests/test_explore_graph.py": 10,
    "tests/test_explore_handlers.py": 11,
    "tests/test_explore_mcp_timeout.py": 13,
    "tests/test_explore_perf_regression_gate.py": 109,
    "tests/test_explore_pipeline.py": 33,
    "tests/test_explore_real_codebase.py": 77,
    "tests/test_filesystem_scenario_baseline.py": 18,
    "tests/test_git_proposal_seal.py": 46,
    "tests/test_git_wrapper.py": 16,
    "tests/test_harness_run_diagnosis.py": 10,
    "tests/test_install_wheel.py": 120,
    "tests/test_integration_clock_and_reasons.py": 11,
    "tests/test_integration_decision_table.py": 119,
    "tests/test_integration_diagnostic.py": 21,
    "tests/test_integration_diagnostic_evidence.py": 29,
    "tests/test_integration_diagnostic_exhaustion.py": 28,
    "tests/test_integration_diagnostic_intervention_cap.py": 26,
    "tests/test_integration_diagnostic_livelock.py": 44,
    "tests/test_integration_diagnostic_resolver_health.py": 166,
    "tests/test_integration_diagnostic_worktree_isolation.py": 18,
    "tests/test_integration_dirt_decisions.py": 12,
    "tests/test_integration_dirt_shapes.py": 21,
    "tests/test_integration_fault_catalogue.py": 143,
    "tests/test_integration_fault_model.py": 74,
    "tests/test_integration_ladder.py": 10,
    "tests/test_integration_ladder_last_resort.py": 14,
    "tests/test_integration_ladder_terminal.py": 11,
    "tests/test_integration_obstruction_real_git.py": 490,
    "tests/test_integration_seam_wiring.py": 15,
    "tests/test_integration_state_coverage.py": 128,
    "tests/test_integration_state_space.py": 69,
    "tests/test_integration_termination_model.py": 14,
    "tests/test_internal_skills_mcp_prompts.py": 15,
    "tests/test_lifecycle_acceptance.py": 22,
    "tests/test_liveness_pa002_properties.py": 21,
    "tests/test_liveness_properties.py": 36,
    "tests/test_liveness_termination.py": 20,
    "tests/test_mcp_endpoint_functional_sweep.py": 90,
    "tests/test_mcp_lifecycle.py": 18,
    "tests/test_mcp_server_exec_result_contract.py": 19,
    "tests/test_md_artifact_editing.py": 9,
    "tests/test_multimodal_cli_parity.py": 11,
    "tests/test_no_anti_drift_recovery_invariants.py": 13,
    "tests/test_no_anti_drift_regression.py": 79,
    "tests/test_no_production_intervention_outcome.py": 24,
    "tests/test_no_superseded_paths_typed_setter_contract.py": 21,
    "tests/test_operation_evidence.py": 9,
    "tests/test_parallel_display.py": 14,
    "tests/test_parallel_display_content_routing.py": 13,
    "tests/test_pending_merge_commit_recovery.py": 80,
    "tests/test_pending_merge_repair.py": 12,
    "tests/test_pending_merge_repair_real_git.py": 147,
    "tests/test_pending_merge_repair_resolver_health.py": 15,
    "tests/test_per_test_budget.py": 36,
    "tests/test_per_worker_logging.py": 9,
    "tests/test_phases_commit_review.py": 352,
    "tests/test_phases_retry_on_stale_session.py": 16,
    "tests/test_pipeline_orchestration_flow.py": 13,
    "tests/test_pipeline_runner.py": 21,
    "tests/test_pipeline_runner_determine_effect.py": 10,
    "tests/test_pipeline_runner_execute_agent_effect_2_a.py": 38,
    "tests/test_pipeline_runner_execute_commit_effect.py": 98,
    "tests/test_pipeline_runner_pipeline_runner_loop_1.py": 129,
    "tests/test_pipeline_runner_timeout.py": 13,
    "tests/test_pipeline_session_bridge.py": 13,
    "tests/test_pro_support_contract.py": 9,
    "tests/test_process_audit.py": 9,
    "tests/test_process_hardening.py": 27,
    "tests/test_prompt_endpoint_advertising.py": 29,
    "tests/test_prompt_materialize_2.py": 30,
    "tests/test_prompt_materialize_3.py": 27,
    "tests/test_prompt_materialize_partial_development_result.py": 17,
    "tests/test_property_a_one_transport_one_behavior.py": 15,
    "tests/test_pty_line_reader_pending_bound.py": 12,
    "tests/test_public_workflow_crash_restart_matrix.py": 18,
    "tests/test_public_workflow_entry_point_matrix.py": 176,
    "tests/test_raw_overflow_display_writes_stay_jsonl.py": 9,
    "tests/test_real_config_guard.py": 37,
    "tests/test_rebase_first_order.py": 15,
    "tests/test_rebase_state_setters_owner.py": 8,
    "tests/test_record_verify_run.py": 38,
    "tests/test_recovery_release_uses_record_store.py": 36,
    "tests/test_retained_resolution_dispatch.py": 103,
    "tests/test_retained_resolution_handoff.py": 474,
    "tests/test_run_loop_pro_integration.py": 11,
    "tests/test_run_loop_retry_diagnostic.py": 72,
    "tests/test_run_loop_setters_only.py": 24,
    "tests/test_runner_auto_integrate_seam.py": 11,
    "tests/test_runner_decision_delegation.py": 23,
    "tests/test_runner_mcp_validation.py": 9,
    "tests/test_runner_output_routing.py": 26,
    "tests/test_runner_phase_transition_banner.py": 18,
    "tests/test_runner_quiet_mode.py": 38,
    "tests/test_scoped_auto_commit.py": 37,
    "tests/test_skills_content.py": 9,
    "tests/test_skills_install_auto_commit.py": 65,
    "tests/test_skills_installer_baseline.py": 16,
    "tests/test_skills_installer_sibling_symlinks.py": 21,
    "tests/test_skills_installer_update.py": 19,
    "tests/test_stale_intent_startup_replay.py": 91,
    "tests/test_timing_safety_transports.py": 28,
    "tests/test_tool_catalog_cache.py": 9,
    "tests/test_tool_exec_handle_exec_command.py": 30,
    "tests/test_tool_exec_process_cleanup.py": 11,
    "tests/test_tool_exec_run_command.py": 23,
    "tests/test_tool_git_read_nested_repo.py": 10,
    "tests/test_tool_git_read_path_validation.py": 44,
    "tests/test_unreadable_record_entry_points.py": 84,
    "tests/test_verify.py": 40,
    "tests/test_verify_budget.py": 71,
    "tests/test_visual_capture.py": 11,
    "tests/test_waiting_state_owner.py": 11,
    "tests/testing/test_audit_sast.py": 95,
    "tests/unit/display/test_palette.py": 17,
    "tests/unit/test_storage_lifecycle.py": 14,
}


if not EXCLUSIVE_SUBPROCESS_E2E_FILES:
    raise RuntimeError("EXCLUSIVE_SUBPROCESS_E2E_FILES must not be empty")
if len(EXCLUSIVE_SUBPROCESS_E2E_FILES) != len(set(EXCLUSIVE_SUBPROCESS_E2E_FILES)):
    raise RuntimeError("EXCLUSIVE_SUBPROCESS_E2E_FILES must not contain duplicates")
if not REQUIRED_AUTO_INTEGRATE_E2E_FILES:
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not be empty")
if len(REQUIRED_AUTO_INTEGRATE_E2E_FILES) != len(set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)):
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not contain duplicates")
if not SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES:
    raise RuntimeError("SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES must not be empty")
if len(SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES) != len(set(SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES)):
    raise RuntimeError("SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES must not contain duplicates")
if _MAX_PYTEST_WORKERS <= 0:
    raise RuntimeError("_MAX_PYTEST_WORKERS must be positive")
if _MAX_XDIST_WORKERS_PER_SHARD < 0:
    raise RuntimeError("_MAX_XDIST_WORKERS_PER_SHARD must be non-negative")
if _REQUIRED_E2E_SHARD_COUNT <= 0:
    raise RuntimeError("_REQUIRED_E2E_SHARD_COUNT must be positive")


def validate_exclusive_subprocess_e2e_selection(selected_files: Iterable[str]) -> None:
    counts = Counter(selected_files)
    missing = tuple(path for path in EXCLUSIVE_SUBPROCESS_E2E_FILES if counts[path] == 0)
    duplicate = tuple(path for path in EXCLUSIVE_SUBPROCESS_E2E_FILES if counts[path] > 1)
    if missing or duplicate:
        details = []
        if missing:
            details.append("missing files: " + ", ".join(missing))
        if duplicate:
            details.append("duplicate files: " + ", ".join(duplicate))
        raise RuntimeError("invalid exclusive subprocess-E2E selection: " + "; ".join(details))


def validate_required_auto_integrate_selection(selected_files: Iterable[str]) -> None:
    selected = frozenset(selected_files)
    missing = tuple(
        path
        for path in REQUIRED_AUTO_INTEGRATE_E2E_FILES
        if path not in NON_SELECTABLE_REQUIRED_REGISTRY_FILES and path not in selected
    )
    if missing:
        raise RuntimeError(
            "combined pytest selection omitted required auto-integrate E2E files: "
            + ", ".join(missing)
        )


def partition_selected_files(
    selected_files: Iterable[str],
    *,
    worker_count: int,
    file_weights: Mapping[str, int] | None = None,
) -> tuple[tuple[str, ...], ...]:
    if worker_count <= 0:
        raise ValueError("worker_count must be positive")
    ordered_files: tuple[str, ...] = tuple(sorted(set(selected_files)))
    if not ordered_files:
        return ()
    shard_count = min(worker_count, len(ordered_files))
    shards: list[list[str]] = [[] for _ in range(shard_count)]
    shard_weights = [0] * shard_count
    effective_weights: Mapping[str, int] = (
        file_weights if file_weights is not None else dict.fromkeys(ordered_files, 1)
    )
    missing_weights = sorted(set(ordered_files) - set(effective_weights))
    if missing_weights:
        raise RuntimeError("missing test file weights: " + ", ".join(missing_weights))

    weight_map: dict[str, int] = (
        dict(effective_weights) if file_weights is not None else dict.fromkeys(ordered_files, 1)
    )

    def _file_sort_key(path: str) -> tuple[int, str]:
        return (-weight_map[path], path)

    def _shard_sort_key(index: int) -> tuple[int, int]:
        return (shard_weights[index], index)

    for path in sorted(ordered_files, key=_file_sort_key):
        shard_index = min(range(shard_count), key=_shard_sort_key)
        shards[shard_index].append(path)
        shard_weights[shard_index] += weight_map[path]
    assignment = tuple(tuple(sorted(shard)) for shard in shards)
    validate_exact_file_assignment(ordered_files, assignment)
    return assignment


def validate_exact_file_assignment(
    selected_files: Iterable[str],
    shards: Iterable[Iterable[str]],
) -> None:
    selected = set(selected_files)
    assigned_paths = tuple(path for shard in shards for path in shard)
    assigned = set(assigned_paths)
    counts = Counter(assigned_paths)
    duplicate_paths = sorted(path for path, count in counts.items() if count > 1)
    missing_paths = sorted(selected - assigned)
    unexpected_paths = sorted(assigned - selected)
    if duplicate_paths or missing_paths or unexpected_paths:
        details = []
        if duplicate_paths:
            details.append("duplicate files: " + ", ".join(duplicate_paths))
        if missing_paths:
            details.append("missing files: " + ", ".join(missing_paths))
        if unexpected_paths:
            details.append("unexpected files: " + ", ".join(unexpected_paths))
        raise RuntimeError("invalid test shard assignment: " + "; ".join(details))


def _pytest_workers() -> str:
    raw = os.getenv("PYTEST_WORKERS", _DEFAULT_PYTEST_WORKERS)
    available_cores = os.cpu_count() or 2
    worker_cap = (
        _PERFORMANCE_CORE_PYTEST_WORKER_CAP
        if available_cores <= _HETEROGENEOUS_CORE_HOST_MAX_CORES
        else _MAX_PYTEST_WORKERS
    )
    if raw == "auto":
        auto_cores = 1 if available_cores <= _MINIMUM_MULTI_SHARD_CORES else available_cores
        return str(min(worker_cap, auto_cores))
    try:
        requested = int(raw)
    except ValueError:
        return raw
    explicit_max = max(1, available_cores - 2)
    return str(max(1, min(requested, explicit_max)))


def _xdist_workers_per_shard() -> str:
    raw = os.getenv("PYTEST_XDIST_WORKERS_PER_SHARD", _DEFAULT_XDIST_WORKERS_PER_SHARD)
    if raw != "auto":
        return raw
    available_cores = os.cpu_count() or 2
    shard_count = max(1, int(_pytest_workers()))
    per_shard = max(1, available_cores // shard_count)
    return str(min(_MAX_XDIST_WORKERS_PER_SHARD, per_shard))


def _default_spawner(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> ShardProcess:
    return _PYTEST_SHARD_PROCESS_MANAGER.spawn(
        command,
        SpawnOptions(
            cwd=str(cwd),
            env=dict(env),
            stdout=None,
            stderr=None,
            label="verify:pytest-shard",
        ),
    )


def _shard_command(
    *,
    manifest_path: Path,
    basetemp: Path,
    marker_expression: str = _VERIFICATION_MARK_EXPRESSION,
    xdist_workers: str = "0",
) -> tuple[str, ...]:
    use_xdist = xdist_workers.isdigit() and int(xdist_workers) > 0
    if use_xdist:
        xdist_args: tuple[str, ...] = (
            "-n",
            xdist_workers,
            "--dist",
            "loadgroup",
        )
    else:
        xdist_args = ()
    return (
        sys.executable,
        "-m",
        "pytest",
        "tests",
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "-p",
        "ralph.testing.pytest_shard_manifest_plugin",
        "--ralph-shard-manifest",
        str(manifest_path),
        *xdist_args,
        "-m",
        marker_expression,
        "--basetemp",
        str(basetemp),
        *(("--durations", str(_PYTEST_DURATIONS_REPORT)) if _PYTEST_DURATIONS_REPORT > 0 else ()),
    )


def _is_subprocess_e2e_marker(expression: ast.expr) -> bool:
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "subprocess_e2e"
        and isinstance(expression.value, ast.Attribute)
        and expression.value.attr == "mark"
        and isinstance(expression.value.value, ast.Name)
        and expression.value.value.id == "pytest"
    )


def _is_module_subprocess_e2e(source: str, *, filename: str) -> bool:
    if "pytestmark" not in source or "subprocess_e2e" not in source:
        return False
    tree = ast.parse(source, filename=filename)
    final_marker: ast.expr | None = None
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark"
            for target in statement.targets
        ):
            final_marker = statement.value
        elif (
            isinstance(statement, (ast.AnnAssign, ast.AugAssign))
            and isinstance(statement.target, ast.Name)
            and (statement.target.id == "pytestmark")
        ):
            final_marker = None
    if final_marker is None:
        return False
    if _is_subprocess_e2e_marker(final_marker):
        return True
    if not isinstance(final_marker, (ast.List, ast.Tuple, ast.Set)):
        return False
    return any(_is_subprocess_e2e_marker(element) for element in final_marker.elts)


def _discover_all_test_files(cwd: Path) -> tuple[str, ...]:
    tests_root = cwd / "tests"
    selected_files = {
        path.relative_to(cwd).as_posix()
        for pattern in ("test_*.py", "*_test.py")
        for path in tests_root.rglob(pattern)
        if path.is_file()
    }
    if not selected_files:
        raise RuntimeError(f"static pytest discovery selected no files under {tests_root}")
    return tuple(sorted(selected_files))


def _discover_profile_test_files(cwd: Path, *, integration_only: bool) -> tuple[str, ...]:
    integration_prefix = "tests/integration/"
    selected = tuple(
        path
        for path in _discover_all_test_files(cwd)
        if path.startswith(integration_prefix) is integration_only
    )
    if not selected:
        profile_name = _INTEGRATION_PROFILE if integration_only else _UNIT_PROFILE
        raise RuntimeError(f"static {profile_name} pytest discovery selected no files")
    return selected


def discover_unit_test_files(cwd: Path) -> tuple[str, ...]:
    return _discover_profile_test_files(cwd, integration_only=False)


def discover_integration_test_files(cwd: Path) -> tuple[str, ...]:
    return _discover_profile_test_files(cwd, integration_only=True)


def discover_fast_test_files(cwd: Path) -> tuple[str, ...]:
    missing_files = tuple(path for path in FAST_PROFILE_TEST_FILES if not (cwd / path).is_file())
    if missing_files:
        raise RuntimeError(f"static fast pytest discovery missing files: {missing_files}")
    return FAST_PROFILE_TEST_FILES


_DISCOVERY_WORKER_CAP: Final = 8
_PARALLEL_DISCOVERY_THRESHOLD: Final = 32
_DISCOVERY_CHUNK_SIZE: Final = 64
_DISCOVERY_FORK_CONTEXT = multiprocessing.get_context("fork")


def _inspect_test_file(
    payload: tuple[str, str, bool],
) -> tuple[str, str, int, bool]:
    cwd_str, path, is_required = payload
    cwd = Path(cwd_str)
    if is_required:
        source = (cwd / path).read_text(encoding="utf-8")
        weight = estimate_test_file_weight(source)
        return path, source, weight, True
    source_bytes = (cwd / path).read_bytes()
    if b"pytestmark" not in source_bytes or b"subprocess_e2e" not in source_bytes:
        source = source_bytes.decode("utf-8")
        weight = _DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES.get(path) or _fast_test_count(source)
        return path, source, weight, True
    source = source_bytes.decode("utf-8")
    if not _is_module_subprocess_e2e(source, filename=path):
        weight = _DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES.get(path) or estimate_test_file_weight(source)
        return path, source, weight, True
    return path, source, 0, False


def discover_test_files(cwd: Path) -> tuple[str, ...]:
    all_files = _discover_all_test_files(cwd)
    req_set = set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    cwd_str = str(cwd)
    payloads = [(cwd_str, path, path in req_set) for path in all_files]
    workers = min(_DISCOVERY_WORKER_CAP, len(payloads), os.cpu_count() or 1)
    if workers <= 1 or len(payloads) < _PARALLEL_DISCOVERY_THRESHOLD:
        results = [_inspect_test_file(p) for p in payloads]
    else:
        with ProcessPoolExecutor(max_workers=workers, mp_context=_DISCOVERY_FORK_CONTEXT) as pool:
            results = list(pool.map(_inspect_test_file, payloads, chunksize=_DISCOVERY_CHUNK_SIZE))

    discovered: list[str] = []
    for path, source, weight, include in results:
        if include:
            _cache_source(path, source)
            _FILE_WEIGHT_CACHE[path] = weight
            discovered.append(path)
    selected = tuple(discovered)
    validate_required_auto_integrate_selection(selected)
    return selected


def discover_subprocess_e2e_files(cwd: Path) -> tuple[str, ...]:
    selected: list[str] = []
    for relative_path in _discover_all_test_files(cwd):
        source_bytes = (cwd / relative_path).read_bytes()
        if b"subprocess_e2e" not in source_bytes:
            continue
        source = source_bytes.decode("utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError:
            continue
        if _contains_subprocess_e2e(tree):
            selected.append(relative_path)
    if not selected:
        raise RuntimeError("static subprocess-E2E discovery selected no files")
    return tuple(selected)


def _contains_subprocess_e2e(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "subprocess_e2e"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "pytest"
        ):
            return True
    return False


def _literal_parametrize_case_count(decorator: ast.expr) -> int:
    if not isinstance(decorator, ast.Call):
        return 1
    target = decorator.func
    if not (
        isinstance(target, ast.Attribute)
        and target.attr == "parametrize"
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "mark"
        and isinstance(target.value.value, ast.Name)
        and target.value.value.id == "pytest"
    ):
        return 1
    cases: ast.expr | None
    if len(decorator.args) > _PARAMETRIZE_CASES_ARGUMENT_INDEX:
        cases = decorator.args[_PARAMETRIZE_CASES_ARGUMENT_INDEX]
    else:
        cases = next(
            (keyword.value for keyword in decorator.keywords if keyword.arg == "argvalues"),
            None,
        )
    if isinstance(cases, ast.List):
        return max(1, len(cases.elts))
    if isinstance(cases, ast.Tuple):
        return max(1, len(cases.elts))
    if isinstance(cases, ast.Set):
        return max(1, len(cases.elts))
    return 1


def estimate_test_file_weight(source: str) -> int:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 1
    weight = 0
    body_stack: list[list[ast.stmt]] = [list(tree.body)]
    while body_stack:
        body = body_stack[-1]
        if not body:
            body_stack.pop()
            continue
        node = body.pop()
        if isinstance(node, ast.ClassDef):
            body_stack.append(list(node.body))
            continue
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        if not node.name.startswith("test_"):
            continue
        multiplier = 1
        for decorator in node.decorator_list:
            multiplier *= _literal_parametrize_case_count(decorator)
        weight += multiplier
    return max(1, weight)


_TEST_DEF_PATTERN = re.compile(r"^(?:\s*)(?:async )?def test_")


def _fast_test_count(source: str) -> int:
    if "parametrize" in source:
        return estimate_test_file_weight(source)
    return sum(1 for line in source.splitlines() if _TEST_DEF_PATTERN.match(line))


def _weight_from_test_count(test_count: int) -> int:
    return max(1, round(test_count * _BASE_SECONDS_PER_TEST / _WEIGHT_SECONDS_PER_UNIT))


def _test_file_weight(cwd: Path, relative_path: str) -> int:
    override = _DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES.get(relative_path)
    if override is not None:
        return override
    test_count = _estimate_weight_from_cache(relative_path)
    if test_count is None:
        source = (cwd / relative_path).read_text(encoding="utf-8")
        test_count = estimate_test_file_weight(source)
    weight = _weight_from_test_count(test_count)
    if relative_path in REQUIRED_AUTO_INTEGRATE_E2E_FILES:
        return weight * _REQUIRED_E2E_WEIGHT_MULTIPLIER
    return weight


_FILE_SOURCE_CACHE: dict[str, str] = {}
_FILE_WEIGHT_CACHE: dict[str, int] = {}


def _cache_source(path: str, source: str) -> None:
    _FILE_SOURCE_CACHE[path] = source


def _estimate_weight_from_cache(path: str) -> int | None:
    cached_source = _FILE_SOURCE_CACHE.get(path)
    if cached_source is None:
        return None
    cached_weight = _FILE_WEIGHT_CACHE.get(path)
    if cached_weight is not None:
        return cached_weight
    weight = estimate_test_file_weight(cached_source)
    _FILE_WEIGHT_CACHE[path] = weight
    return weight


def reset_discovery_cache() -> None:
    _FILE_SOURCE_CACHE.clear()
    _FILE_WEIGHT_CACHE.clear()


def _remaining_seconds(deadline: float, monotonic: Callable[[], float]) -> float:
    return max(0.0, deadline - monotonic())


def _decode_output(output: bytes | None) -> str:
    return output.decode("utf-8", errors="replace") if output else ""


def _reap_process(
    process: ShardProcess,
    *,
    timeout_seconds: float,
) -> tuple[str, str]:
    try:
        stdout, stderr = process.communicate(timeout=max(0.0, timeout_seconds))
    except subprocess.TimeoutExpired:
        process.cleanup_orphans()
        if timeout_seconds <= 0.0:
            return "", ""
        return "", "pytest shard did not exit after termination\n"
    process.cleanup_orphans()
    return _decode_output(stdout), _decode_output(stderr)


def _drain_running_process(
    process: ShardProcess,
    *,
    timeout_seconds: float,
) -> tuple[int, tuple[str, str]] | None:
    try:
        stdout, stderr = process.communicate(timeout=max(0.0, timeout_seconds))
    except subprocess.TimeoutExpired:
        return None
    returncode = process.poll()
    if returncode is None:
        return None
    process.cleanup_orphans()
    return returncode, (_decode_output(stdout), _decode_output(stderr))


def _terminate_and_reap(
    processes: Sequence[ShardProcess],
    *,
    deadline: float,
    monotonic: Callable[[], float],
) -> tuple[tuple[str, str], ...]:
    for process in processes:
        if process.poll() is None:
            try:
                process.terminate(grace_period_s=0.0)
            except Exception as exc:
                print(
                    f"warning: shard terminate raised {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
    outputs = []
    for process in processes:
        remaining = min(
            _SHARD_TERMINATION_DRAIN_SECONDS,
            _remaining_seconds(deadline, monotonic),
        )
        outputs.append(_reap_process(process, timeout_seconds=remaining))
    return tuple(outputs)


def _print_shard_outputs(outputs: Sequence[tuple[str, str]]) -> None:
    for stdout, stderr in outputs:
        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr:
            print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr)


def _print_timeout_diagnostics(
    shards: Sequence[Sequence[str]],
    processes: Sequence[ShardProcess],
    *,
    started_at: float,
    monotonic: Callable[[], float],
) -> None:
    elapsed_seconds = monotonic() - started_at
    for index, (shard, process) in enumerate(zip(shards[: len(processes)], processes, strict=True)):
        if process.poll() is None:
            print(
                f"pytest shard {index} timed out after {elapsed_seconds:.2f}s; "
                f"files: {', '.join(shard)}",
                file=sys.stderr,
            )


def _finalize_shard_returncode(
    processes: Sequence[ShardProcess],
    *,
    empty_selection_returncode: int | None,
) -> int:
    if empty_selection_returncode is None:
        return 0
    returncodes = tuple(process.poll() for process in processes)
    if returncodes and all(returncode == empty_selection_returncode for returncode in returncodes):
        return empty_selection_returncode
    return 0


def _run_shards(
    shards: Sequence[Sequence[str]],
    *,
    cwd: Path,
    env: Mapping[str, str],
    basetemp_root: Path,
    deadline: float,
    started_at: float,
    spawner: ShardSpawner,
    monotonic: Callable[[], float],
    wait: Callable[[float], None],
    marker_expression: str = _VERIFICATION_MARK_EXPRESSION,
    xdist_workers: str = "0",
    successful_returncodes: frozenset[int] = frozenset((0,)),
    empty_selection_returncode: int | None = None,
) -> int:
    processes: list[ShardProcess] = []
    try:
        for shard_index, shard in enumerate(shards):
            if _remaining_seconds(deadline, monotonic) <= 0:
                _print_timeout_diagnostics(
                    shards, processes, started_at=started_at, monotonic=monotonic
                )
                outputs = _terminate_and_reap(
                    processes,
                    deadline=deadline,
                    monotonic=monotonic,
                )
                _print_shard_outputs(outputs)
                return TIMEOUT_EXIT_CODE
            manifest_path = basetemp_root / f"shard-{shard_index}.manifest"
            manifest_path.write_text(
                "".join(f"{path}\n" for path in shard),
                encoding="utf-8",
            )
            print(
                f"pytest shard {shard_index}: {len(shard)} files, "
                f"xdist={xdist_workers}, started at {monotonic() - started_at:.2f}s",
                flush=True,
            )
            shard_basetemp = basetemp_root / f"shard-{shard_index}"
            shard_basetemp.mkdir(mode=0o700, parents=True, exist_ok=True)
            processes.append(
                spawner(
                    _shard_command(
                        manifest_path=manifest_path,
                        basetemp=shard_basetemp,
                        marker_expression=marker_expression,
                        xdist_workers=xdist_workers,
                    ),
                    cwd=cwd,
                    env=env,
                )
            )
    except OSError as exc:
        outputs = _terminate_and_reap(processes, deadline=deadline, monotonic=monotonic)
        _print_shard_outputs(outputs)
        print(f"failed to start pytest shard: {exc}", file=sys.stderr)
        return 2

    completed: dict[int, tuple[str, str]] = {}
    while len(completed) < len(processes):
        if _remaining_seconds(deadline, monotonic) <= 0:
            _print_timeout_diagnostics(
                shards, processes, started_at=started_at, monotonic=monotonic
            )
            outputs = _terminate_and_reap(
                processes,
                deadline=deadline,
                monotonic=monotonic,
            )
            _print_shard_outputs(outputs)
            return TIMEOUT_EXIT_CODE
        for index, process in enumerate(processes):
            if index in completed:
                continue
            returncode = process.poll()
            if returncode is None:
                drained = _drain_running_process(
                    process,
                    timeout_seconds=min(
                        _SHARD_POLL_INTERVAL_SECONDS,
                        _remaining_seconds(deadline, monotonic),
                    ),
                )
                if drained is None:
                    continue
                returncode, completed[index] = drained
            else:
                completed[index] = _reap_process(
                    process,
                    timeout_seconds=_remaining_seconds(deadline, monotonic),
                )
            print(
                f"pytest shard {index}: exit={returncode}, elapsed={monotonic() - started_at:.2f}s",
                flush=True,
            )
            if returncode not in successful_returncodes:
                siblings = [
                    sibling
                    for sibling_index, sibling in enumerate(processes)
                    if sibling_index not in completed
                ]
                sibling_outputs = _terminate_and_reap(
                    siblings,
                    deadline=deadline,
                    monotonic=monotonic,
                )
                all_outputs = [
                    completed.get(output_index, ("", "")) for output_index in range(len(processes))
                ]
                for sibling_index, output in zip(
                    (
                        candidate
                        for candidate in range(len(processes))
                        if candidate not in completed
                    ),
                    sibling_outputs,
                    strict=True,
                ):
                    all_outputs[sibling_index] = output
                _print_shard_outputs(all_outputs)
                return returncode
        if len(completed) < len(processes):
            wait(min(_SHARD_POLL_INTERVAL_SECONDS, _remaining_seconds(deadline, monotonic)))

    _print_shard_outputs([completed[index] for index in range(len(processes))])
    return _finalize_shard_returncode(
        processes,
        empty_selection_returncode=empty_selection_returncode,
    )


def _select_test_files(
    *,
    cwd: Path,
    file_discoverer: Callable[[Path], tuple[str, ...]],
    profile: str | None,
    auto_integrate_e2e_only: bool,
    subprocess_e2e_only: bool,
) -> tuple[tuple[str, ...], str, tuple[str, ...], int | None]:
    if profile is not None and profile not in _FOCUSED_PROFILES:
        raise ValueError(f"unknown test-suite profile: {profile}")
    active_selector_count = sum((auto_integrate_e2e_only, subprocess_e2e_only, profile is not None))
    if active_selector_count > 1:
        raise ValueError("test-suite profiles are mutually exclusive")
    if profile == _UNIT_PROFILE:
        return (
            discover_unit_test_files(cwd),
            _FOCUSED_PROFILE_MARK_EXPRESSION,
            (),
            None,
        )
    if profile == _INTEGRATION_PROFILE:
        return (
            discover_integration_test_files(cwd),
            _FOCUSED_PROFILE_MARK_EXPRESSION,
            (),
            None,
        )
    if profile == _FAST_PROFILE:
        return (
            discover_fast_test_files(cwd),
            _FOCUSED_PROFILE_MARK_EXPRESSION,
            (),
            None,
        )
    if auto_integrate_e2e_only:
        return (
            tuple(
                path
                for path in REQUIRED_AUTO_INTEGRATE_E2E_FILES
                if path not in NON_SELECTABLE_REQUIRED_REGISTRY_FILES
            ),
            _VERIFICATION_MARK_EXPRESSION,
            (),
            None,
        )
    if subprocess_e2e_only:
        selected_files = discover_subprocess_e2e_files(cwd)
        validate_exclusive_subprocess_e2e_selection(selected_files)
        exclusive_files = tuple(
            path for path in selected_files if path in set(EXCLUSIVE_SUBPROCESS_E2E_FILES)
        )
        general_files = tuple(
            path for path in selected_files if path not in set(EXCLUSIVE_SUBPROCESS_E2E_FILES)
        )
        return (
            general_files,
            _SUBPROCESS_E2E_MARK_EXPRESSION,
            exclusive_files,
            0,
        )
    discovered_files = file_discoverer(cwd)
    validate_required_auto_integrate_selection(discovered_files)
    required_files = tuple(
        path for path in discovered_files if path in set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    )
    selected_files = tuple(
        path for path in discovered_files if path not in set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    )
    required_shard_count = _required_e2e_shard_count() if required_files else None
    return (selected_files, _VERIFICATION_MARK_EXPRESSION, required_files, required_shard_count)


def run_test_suites(
    *,
    cwd: Path,
    suite_timeout_seconds: float = DEFAULT_SUITE_TIMEOUT_SECONDS,
    spawner: ShardSpawner = _default_spawner,
    file_discoverer: Callable[[Path], tuple[str, ...]] = discover_test_files,
    file_weigher: Callable[[Path, str], int] = _test_file_weight,
    monotonic: Callable[[], float] = time.monotonic,
    wait: Callable[[float], None] = time.sleep,
    auto_integrate_e2e_only: bool = False,
    subprocess_e2e_only: bool = False,
    profile: str | None = None,
) -> int:
    started_at = monotonic()
    deadline = started_at + suite_timeout_seconds
    inherited_env = dict(os.environ)
    pythonpath = inherited_env.get("PYTHONPATH")
    if pythonpath is None or Path(pythonpath).resolve() != cwd.resolve():
        inherited_env.pop("PYTHONPATH", None)
    env = build_timeout_env(
        base_env=inherited_env,
        test_timeout_seconds=timeout_seconds_from_env(
            TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS
        ),
        suite_timeout_seconds=suite_timeout_seconds,
    )
    (
        selected_files,
        marker_expression,
        required_e2e_shard,
        required_e2e_shard_count,
    ) = _select_test_files(
        cwd=cwd,
        file_discoverer=file_discoverer,
        profile=profile,
        auto_integrate_e2e_only=auto_integrate_e2e_only,
        subprocess_e2e_only=subprocess_e2e_only,
    )

    shards = partition_selected_files(
        selected_files,
        worker_count=int(_pytest_workers()),
        file_weights={path: file_weigher(cwd, path) for path in selected_files},
    )
    required_e2e_shards: tuple[tuple[str, ...], ...] = ()
    if required_e2e_shard:
        if required_e2e_shard_count and required_e2e_shard_count > 1:
            required_e2e_shards = partition_selected_files(
                required_e2e_shard,
                worker_count=required_e2e_shard_count,
                file_weights={path: file_weigher(cwd, path) for path in required_e2e_shard},
            )
        else:
            required_e2e_shards = (required_e2e_shard,)

    if required_e2e_shards:
        validate_exact_file_assignment(
            (*selected_files, *(path for shard in required_e2e_shards for path in shard)),
            (*shards, *required_e2e_shards),
        )
    else:
        validate_exact_file_assignment(selected_files, shards)
    print(
        f"pytest preparation: {len(selected_files) + len(required_e2e_shard)} files, "
        f"{len(shards) + len(required_e2e_shards)} shards in {monotonic() - started_at:.2f}s",
        flush=True,
    )
    temp_directory_prefix: str
    if profile is not None:
        temp_directory_prefix = profile
    elif auto_integrate_e2e_only:
        temp_directory_prefix = "auto-integrate-e2e"
    elif subprocess_e2e_only:
        temp_directory_prefix = "subprocess-e2e"
    else:
        temp_directory_prefix = "verification"
    basetemp_parent = Path(tempfile.gettempdir()) / "ralph-pytest-shards"
    basetemp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"{temp_directory_prefix}-",
        dir=basetemp_parent,
    ) as basetemp_root:
        basetemp_path = Path(basetemp_root)
        successful_returncodes = frozenset((0, 5)) if profile is not None else frozenset((0,))
        empty_selection_returncode = 5 if profile is not None else None
        run_required_concurrently = bool(required_e2e_shards and not subprocess_e2e_only)
        all_shards = (*shards, *required_e2e_shards) if run_required_concurrently else shards
        general_result = _run_shards(
            all_shards,
            cwd=cwd,
            env=env,
            basetemp_root=basetemp_path,
            deadline=deadline,
            started_at=started_at,
            spawner=spawner,
            monotonic=monotonic,
            wait=wait,
            marker_expression=marker_expression,
            xdist_workers=_xdist_workers_per_shard(),
            successful_returncodes=successful_returncodes,
            empty_selection_returncode=empty_selection_returncode,
        )
        if general_result not in successful_returncodes:
            return general_result
        if not required_e2e_shards or run_required_concurrently:
            return general_result
        if subprocess_e2e_only:
            xdist_workers_for_required = _EXCLUSIVE_SUBPROCESS_E2E_SHARD_XDIST_WORKERS
        else:
            xdist_workers_for_required = _xdist_workers_per_shard()
        return _run_shards(
            (required_e2e_shards[0],),
            cwd=cwd,
            env=env,
            basetemp_root=basetemp_path,
            deadline=deadline,
            started_at=started_at,
            spawner=spawner,
            monotonic=monotonic,
            wait=wait,
            marker_expression=marker_expression,
            xdist_workers=xdist_workers_for_required,
            successful_returncodes=successful_returncodes,
            empty_selection_returncode=empty_selection_returncode,
        )


def _run_durations_profile(cwd: Path) -> int:
    inherited_env = dict(os.environ)
    pythonpath = inherited_env.get("PYTHONPATH")
    if pythonpath is None or Path(pythonpath).resolve() != cwd.resolve():
        inherited_env.pop("PYTHONPATH", None)
    env = build_timeout_env(
        base_env=inherited_env,
        test_timeout_seconds=timeout_seconds_from_env(
            TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS
        ),
        suite_timeout_seconds=DEFAULT_SUITE_TIMEOUT_SECONDS,
    )
    seen_files: dict[str, None] = {}
    for test_file in (*discover_test_files(cwd), *REQUIRED_AUTO_INTEGRATE_E2E_FILES):
        seen_files[test_file] = None
    files = tuple(seen_files)
    basetemp_parent = Path(tempfile.gettempdir()) / "ralph-pytest-durations"
    basetemp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="durations-", dir=basetemp_parent) as tmp_root:
        for index, relative_path in enumerate(files):
            command = (
                sys.executable,
                "-m",
                "pytest",
                relative_path,
                "-q",
                "--no-header",
                "-p",
                "no:cacheprovider",
                "--durations=0",
                "--durations-min=0.0001",
                "--basetemp",
                str(Path(tmp_root) / str(index)),
            )
            file_started_at = time.monotonic()
            process = _default_spawner(command, cwd=cwd, env=env)
            _reap_process(process, timeout_seconds=_DURATIONS_PER_FILE_TIMEOUT_SECONDS)
            print(f"{relative_path}: {time.monotonic() - file_started_at}", flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    sanitize_process_environment()
    arguments = tuple(argv or ())
    if arguments == ("--auto-integrate-e2e",):
        return run_test_suites(cwd=Path.cwd(), auto_integrate_e2e_only=True)
    if arguments == ("--subprocess-e2e",):
        return run_test_suites(cwd=Path.cwd(), subprocess_e2e_only=True)
    if arguments == ("--profile", _DURATIONS_PROFILE):
        return _run_durations_profile(cwd=Path.cwd())
    if (
        len(arguments) == _PROFILE_OPTION_ARGUMENT_COUNT
        and arguments[0] == "--profile"
        and arguments[1] in _FOCUSED_PROFILES
    ):
        return run_test_suites(cwd=Path.cwd(), profile=arguments[1])
    if arguments:
        raise SystemExit(
            "ralph.test_suites accepts --profile unit, --profile integration, --profile fast, or --profile durations, "
            "--auto-integrate-e2e, or --subprocess-e2e"
        )
    return run_test_suites(cwd=Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
