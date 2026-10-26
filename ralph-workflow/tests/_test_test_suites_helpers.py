"""Shared fakes and registry mirror for the test-suite runner suites.

The split lives here, not in :mod:`ralph.testing`, because the fakes only
exist to exercise the maintained pytest-shard runner. Keeping them in a
``_``-prefixed module means pytest does not collect this file (the project
default discovery patterns are ``test_*.py`` / ``*_test.py``), so the
helpers carry zero per-shard collection cost. Every test file that
imports them pays the same import cost once via pytest's import cache.

The module also owns ``EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES`` and
``EXPECTED_SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES``, the single mirrors of
``ralph.test_suites.REQUIRED_AUTO_INTEGRATE_E2E_FILES`` and
``ralph.test_suites.SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES``.
Both :mod:`tests.test_test_suites` and
:mod:`tests.test_test_suites_orchestration` assert equality against these
one copies, so a registry drift fails in both suites and the two suites
cannot disagree about the expected registries.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

#: The required auto-integrate E2E registry, mirrored from
#: ``ralph.test_suites.REQUIRED_AUTO_INTEGRATE_E2E_FILES``. Both the
#: discovery suite and the orchestration suite assert equality against
#: this mirror so a registry drift fails in BOTH modules.
EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES: tuple[str, ...] = (
    "tests/test_auto_integrate_end_to_end.py",
    # wt-03-fix-bug (WU-R1): detached-HEAD recovery uses the
    # worktree-private reflog to reconstruct the original branch.
    # The fail-closed exact-tip invariant must stay on the default
    # ``make test`` profile so the permissive-ancestor /
    # fall-through-to-older-entry regression cannot rot silently.
    "tests/test_auto_integrate_recovery_git_state_reflog.py",
    "tests/test_integration_obstruction_real_git.py",
    "tests/test_pending_merge_commit_recovery.py",
    "tests/test_pending_merge_repair_real_git.py",
    "tests/test_retained_resolution_handoff.py",
    "tests/test_retained_resolution_dispatch.py",
    "tests/integration/test_retained_integration_acceptance.py",
    "tests/test_auto_integrate_acceptance_scenarios_e2e.py",
    "tests/test_auto_rebase_idempotence_e2e.py",
    "tests/test_backup_ref_operation_scope.py",
    "tests/test_tool_git_read_path_validation.py",
    "tests/test_commit_cleanup_invariants.py",
    "tests/test_commit_cleanup_verify_gate.py",
    "tests/integration/test_pipeline_commit_cleanup_to_commit_e2e.py",
    "tests/test_explore_crash_safety.py",
    "tests/test_explore_concurrency.py",
    "tests/test_explore_cold_start_lifecycle.py",
    "tests/test_explore_perf_regression_gate.py",
    # wt-012: the deterministic auto-commit acceptance cases for the
    # skill and policy surfaces are subprocess_e2e (real-git boundary
    # exercised end-to-end) but must stay on the default ``make test``
    # profile so the isolation / rollback contract cannot rot
    # silently.
    "tests/test_skills_auto_commit.py",
    # wt-012: the producer-level install + auto-commit boundary cases
    # are split from test_skills_auto_commit; same registry contract.
    "tests/test_skills_install_auto_commit.py",
    "tests/project_policy/test_policy_auto_commit.py",
    # wt-012: the shared primitive's own real-git regression cases
    # (exact-path HEAD lookup, dir→symlink transition, FAILED
    # rollback, ancestor-symlink safety). Real-git boundary
    # required for the prefix-collision and dir→symlink shapes; the
    # default ``make test`` profile is the only way the contract
    # cannot rot silently.
    "tests/test_scoped_auto_commit.py",
    # wt-012: the deterministic auto-commit acceptance cases for the
    # config write sites (migration, autowire, enable, init
    # PROMPT.md). subprocess_e2e (real-git boundary exercised
    # end-to-end) but must stay on the default ``make test`` profile
    # so the config-write isolation / rollback contract cannot rot
    # silently.
    "tests/test_config_auto_commit.py",
    # wt-12: the bootstrap config-write isolation cases (Unit 1) and
    # the parameterized clean-tree acceptance cases for the
    # non-bootstrap deterministic writers (skill sync, skill install,
    # policy preflight, gitignore seed, config autowire). Real-git
    # subprocess_e2e surfaces that MUST stay on the default ``make
    # test`` profile so the clean-tree / fixed-subject contract cannot
    # rot silently. In-budget on the 60s combined verify budget.
    "tests/test_config_bootstrap_git_isolation.py",
    "tests/test_deterministic_writers_clean_tree.py",
    # wt-15-policy (U1): the 460-test parametrized display module is too
    # heavy for the 16-shard LPT partition (it lands on the slowest
    # low-file-count shard, costing ~5s of wall clock on a 40-core host).
    # Moving it here lets the E2E shard's eight xdist workers absorb
    # the parametrization fan-out, so the slowest general shard and
    # the E2E shard both fit inside the 60-second combined budget
    # verified by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``.
    "tests/display/test_generated_scene_catalog.py",
    "tests/display/test_generated_scene_renderer.py",
    "tests/display/test_generated_scene_salience.py",
    # wt-03-fix-bug (S): the linked-worktree regression test for the
    # diagnostic-evidence collector. The collector's architectural
    # fix is the prohibition of repository-global reads; this test
    # reproduces the production scenario in a tmp_path repo with a
    # linked worktree and asserts the operation-scoped evidence
    # does not shift when an unrelated branch advances.
    "tests/test_integration_diagnostic_worktree_isolation.py",
    "tests/agents/invoke/test_real_pty_completion_no_terminal_lost.py",
    # wt-03-fix-bug (WU-5): the package-wide ``live_tree`` regression
    # gates in the opts-key and kwargs-forwarding audits each scan
    # the full shipped tree. They are slow under a single pytest
    # worker (~6 s for opts-key drift, ~2.5 s for kwargs-forwarding)
    # and would tip a single-file shard past the 60 s combined
    # budget even when every other shard finishes inside it. Moving
    # them to the E2E shard gives the eight in-shard xdist workers
    # enough headroom to absorb the scan, and keeps the regression
    # gates on the default ``make test`` profile so the silent-key
    # drop cannot rot. They are marked ``subprocess_e2e`` so the
    # test-policy audit treats the real-tree scan as an external
    # boundary rather than a black-box test seam.
    "tests/test_audit_opts_key_drift.py",
    "tests/test_audit_kwargs_forwarding.py",
    # wt-03-fix-bug (U4a): real-git regression pinning acceptance
    # scenarios 1 (linked worktree) and 2 (target movement) on the
    # public-boundary decision surface. Real-git, in-budget, must
    # run on the default ``make test`` profile so the linked-
    # worktree obligation retention cannot rot silently.
    "tests/test_authority_audit_scenario_1_2_worktree_target.py",
    # wt-16: root-cause + containment regression tests for the
    # MCP respawn bug (R1-R4, S1-S4). See
    # ``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` in
    # ``ralph/test_suites.py`` for the rationale on why the
    # E2E shard is the right home for these.
    "tests/mcp/test_reserved_socket_held_across_respawn.py",
    "tests/mcp/test_mcp_endpoint_never_refuses.py",
    "tests/mcp/test_mcp_slow_startup_served_from_backlog.py",
    "tests/mcp/test_respawn_no_discovery.py",
    "tests/mcp/test_mcp_server_startup_stages_observable.py",
    "tests/mcp/test_mcp_server_last_stage_when_stage_file_missing.py",
    "tests/mcp/test_mcp_stage_attribution_real_child.py",
    "tests/mcp/test_restart_aware_bridge_failed_restart_fn.py",
    "tests/mcp/test_restart_aware_bridge_policy_cooldown.py",
    # wt-16 WU-4: additional respawn regression gates
    "tests/mcp/test_runtime_respawn_precheck.py",
    "tests/mcp/test_discovery_aggregate_budget.py",
    "tests/mcp/test_start_stage_tracker_fresh_spawn.py",
    "tests/mcp/test_preflight_error_carries_stage_and_elapsed.py",
    # wt-16 WU-R2-ENVELOPE: whole-startup budget envelope — child blocked at
    # registry_built surfaces typed McpServerError with last_stage and elapsed_s
    "tests/mcp/test_runtime_blocked_registry_built_surfaces_typed_error.py",
    "tests/mcp/test_effective_config_fingerprint.py",
    # wt-16 (WU-B follow-up): both files shell out to real git in tmp_path
    # repos and now carry the ``subprocess_e2e`` module marker (enforced by
    # the process-audit AST exemption), so they MUST be registered here to
    # stay on the default ``make test`` profile — otherwise discovery
    # excludes them and their coverage silently rots.
    "tests/test_display_lifecycle_label_taxonomy.py",
    "tests/test_unreadable_record_entry_points.py",
    # wt-16: CLI-child banner visibility and bounded catalog publish/FD regressions.
    "tests/mcp/test_cli_child_banner_visible_without_unbuffered_env.py",
    # wt-16 WU-1 / Unit V: real-child startup budget, bounded catalog publish,
    # and fail-closed inherited FD adoption regressions.
    "tests/mcp/test_whole_startup_budget_production.py",
    "tests/mcp/test_bounded_catalog_publish.py",
    "tests/mcp/test_inherited_fd_invalid_fails_closed.py",
    # wt-16 WU-R4: wire-ledger fail-closed OSError path
    "tests/mcp/test_wire_ledger_fail_closed.py",
    # wt-16 WU-Probe: probe-overrun self-measurement
    "tests/mcp/test_probe_overrun_self_measure.py",
    # wt-16 WU-Bridge: ATTEMPT_FAILED is non-terminal; only BUDGET_EXHAUSTED
    # raises McpServerError. The C2 fix pins the new contract so a single
    # failed restart_fn call does NOT end the agent run.
    "tests/mcp/test_bridge_attempt_vs_budget.py",
    # wt-16 make-before-break MCP restart resilience test suites:
    "tests/mcp/test_bridge_make_before_break.py",
    "tests/mcp/test_bridge_e1_e2_edge_cases.py",
    "tests/mcp/test_bridge_e3_e4_edge_cases.py",
    "tests/mcp/test_bridge_e5_e6_e7_edge_cases.py",
    # wt-16 make-before-break MCP edge case & integration test suites:
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
    # wt-16 WU-Plan: spawn-environment helper extraction regresses the typed-error
    # boundary for ``_spawn_mcp_process`` so a fault inside the helper before
    # ``deps.spawn_process`` propagates the typed error (no UnboundLocalError),
    # leaves the session file removed, and closes both socket ends it owns.
    # subprocess_e2e because the helper boundary is real-child-coupled.
    "tests/mcp/test_spawn_early_failure_typed.py",
    # wt-19-fix-long-exec-tasks — exec job flow smokes (real subprocess)
    # Real-process proofs for the liveness assessor + job registry. Both
    # files carry subprocess_e2e + required_auto_integrate_e2e markers.
    "tests/test_exec_smoke_subprocess_e2e.py",
    "tests/test_exec_job_shutdown_subprocess_e2e.py",
)

#: Mirror of ``SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES`` in
#: ``ralph/test_suites.py`` -- the audit-exception registry for
#: default-lane files whose fixtures build real repositories
#: (``tests/conftest.py`` cannot be discovered by pytest at all, so
#: these entries live outside ``EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES``
#: above). Both the discovery suite and the orchestration suite pin
#: their own copy so a drift fails in BOTH modules.
EXPECTED_SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES: tuple[str, ...] = (
    "tests/conftest.py",
    "tests/pipeline/test_reducer_lock_retry.py",
    "tests/test_auto_integrate_gates.py",
    "tests/test_bootstrap_auto_seed_git_exclude.py",
    "tests/test_bootstrap_auto_seed_gitignore.py",
    "tests/test_commit_cleanup_diff.py",
    "tests/test_commit_detached_head_recovery.py",
    "tests/test_commit_executor.py",
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
    # OpenCode's prompt is delivered on stdin, so a process fake must
    # satisfy the ``_SyncProcessLike`` protocol's ``stdin`` member.
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
    """Fake shard blocked writing to a full stdout PIPE.

    Models the confirmed pytest-shard PIPE backpressure deadlock: the child
    has finished its work but cannot report an exit status because it is
    blocked in ``write`` on a full pipe. Its completion only becomes visible
    to ``poll()`` once the parent starts draining via ``communicate()``.
    """

    # OpenCode's prompt is delivered on stdin, so a process fake must
    # satisfy the ``_SyncProcessLike`` protocol's ``stdin`` member.
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
