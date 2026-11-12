from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES: tuple[str, ...] = (
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
    "tests/test_skills_auto_commit.py",
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

EXPECTED_SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES: tuple[str, ...] = (
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


class _FakeShardProcess:
    stdin = None

    def __init__(
        self,
        returncodes: list[int | None],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        communicate_times_out: bool = False,
        on_communicate: Callable[[], None] | None = None,
        terminate_raises: BaseException | None = None,
    ) -> None:
        self._returncodes = list(returncodes)
        self._last_returncode: int | None = None
        self._stdout = stdout
        self._stderr = stderr
        self._communicate_times_out = communicate_times_out
        self._on_communicate = on_communicate
        self._terminate_raises = terminate_raises
        self.terminated = False
        self.reaped = False
        self.orphans_cleaned = False

    def poll(self) -> int | None:
        if self._returncodes:
            self._last_returncode = self._returncodes.pop(0)
        return self._last_returncode

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        if self._on_communicate is not None:
            self._on_communicate()
        if self._communicate_times_out:
            raise subprocess.TimeoutExpired(("pytest",), 1.0)
        self.reaped = True
        return self._stdout, self._stderr

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        if self._terminate_raises is not None:
            raise self._terminate_raises
        self.terminated = True
        self._last_returncode = -15

    def cleanup_orphans(self) -> None:
        self.orphans_cleaned = True


class _BackpressuredShardProcess:
    stdin = None

    def __init__(self, *, stdout: bytes = b"shard output\n", stderr: bytes = b"") -> None:
        self._stdout = stdout
        self._stderr = stderr
        self._drained = False
        self.terminated = False
        self.reaped = False
        self.orphans_cleaned = False

    def poll(self) -> int | None:
        return 0 if self._drained else None

    def communicate(
        self,
        input: bytes | None = None,
        timeout: float | None = None,
    ) -> tuple[bytes, bytes]:
        del input, timeout
        self._drained = True
        self.reaped = True
        return self._stdout, self._stderr

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True

    def cleanup_orphans(self) -> None:
        self.orphans_cleaned = True


class _StubSpawner:
    def __init__(self, processes: Sequence[_FakeShardProcess | _BackpressuredShardProcess]) -> None:
        self._processes = list(processes)
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []
        self.manifest_files: list[tuple[str, ...]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> _FakeShardProcess | _BackpressuredShardProcess:
        self.calls.append((tuple(command), cwd, dict(env)))
        if "--ralph-shard-manifest" in command:
            manifest_index = command.index("--ralph-shard-manifest") + 1
            manifest_path = Path(command[manifest_index])
            self.manifest_files.append(
                tuple(manifest_path.read_text(encoding="utf-8").splitlines())
            )
        return self._processes.pop(0)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds
