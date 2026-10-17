"""Run the maintained pytest verification suite under the current interpreter.

.. note::

    The 60-second ABSOLUTE and IMMUTABLE combined test budget is enforced
    UPSTREAM by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`` via cumulative
    ``time.monotonic()`` tracking. This module additionally owns one parent
    deadline across all concurrent file shards. Splitting tests into shards
    does NOT increase either budget.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

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
        """Controllable pytest shard process."""

        def poll(self) -> int | None: ...

        def communicate(
            self,
            input: bytes | None = None,
            timeout: float | None = None,
        ) -> tuple[bytes | None, bytes | None]: ...

        def terminate(self, grace_period_s: float | None = None) -> None: ...

        def cleanup_orphans(self) -> None: ...

    class ShardSpawner(Protocol):
        """Spawn seam for one plain-pytest shard."""

        def __call__(
            self,
            command: Sequence[str],
            *,
            cwd: Path,
            env: Mapping[str, str],
        ) -> ShardProcess: ...


# The 1.0 s per-test ITIMER_REAL budget charges wall clock. Each shard runs
# pytest with xdist (``-n N`` per shard) so the in-shard wall clock scales
# with the host's per-shard worker budget rather than with the count of
# files in the shard. The default profile uses one plain pytest process per
# shard. Operators may override ``PYTEST_WORKERS`` for smaller hosts.
_PYTEST_SHARD_PROCESS_MANAGER = ProcessManager(
    policy=ProcessManagerPolicy(log_events=False, enable_zombie_reaper=False)
)
_DEFAULT_PYTEST_WORKERS = "auto"
# Hard cap on the number of plain-pytest shards; raising this cap does NOT
# raise the combined 60-second budget tracked upstream in
# ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``. The 8-shard cap is the
# verified-only size that keeps the maintained 40-core host's full
# selected suite inside the immutable 60 s combined budget while
# preserving exact-once selection; any larger value exhausts the budget
# on the grown E2E set. The 60s combined budget tracked by
# ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`` is preserved; this is
# a runner-architecture choice, not a budget-constant change.
_HETEROGENEOUS_CORE_HOST_MAX_CORES = 12


def _max_pytest_workers_for_host() -> int:
    return 8


_MAX_PYTEST_WORKERS = _max_pytest_workers_for_host()
# The maintained 12-core host completes the suite with 10 shards under
# _PERFORMANCE_CORE_PYTEST_WORKER_CAP; hosts with >12 cores are bounded by
# _MAX_PYTEST_WORKERS (8 shards) to avoid startup and filesystem contention.
_PERFORMANCE_CORE_PYTEST_WORKER_CAP = 10
_MINIMUM_MULTI_SHARD_CORES = 2
# Default in-shard xdist worker count is ``"0"`` (plain pytest per shard)
# because on the maintained 8-shard CI profile the shard-saturated
# 8-shard fan-out already uses one pytest process per shard and adding
# xdist workers inside each shard shifts wall-clock budget from
# parallel-IO back into pytest-coordination overhead. Operators may
# override with ``PYTEST_XDIST_WORKERS_PER_SHARD=auto`` for the legacy
# CPU-utilisation-maximising policy or an explicit integer for a custom
# in-shard fan-out.
_DEFAULT_XDIST_WORKERS_PER_SHARD = "0"
# Hard cap on the number of pytest-xdist workers spawned INSIDE each shard.
# Combined with ``_MAX_PYTEST_WORKERS`` (8 shards) this gives a maximum
# fan-out of 8 * 4 = 32 workers when ``_DEFAULT_XDIST_WORKERS_PER_SHARD``
# is overridden to ``"auto"`` or a positive integer. The default plain-
# pytest path keeps the slowest shard under the 60s combined budget on
# 8-shard CI without coordination overhead.
_MAX_XDIST_WORKERS_PER_SHARD = 4

#: Exact subprocess-E2E files required by the authoritative verification
#: profile. This registry also drives the focused Make target, so the two
#: selections cannot drift.
EXCLUSIVE_SUBPROCESS_E2E_FILES: tuple[str, ...] = (
    "tests/agents/test_terminal_state_restored_on_exit.py",
)

REQUIRED_AUTO_INTEGRATE_E2E_FILES: tuple[str, ...] = (
    # One real-git landing journey retains the external Git boundary proof;
    # decision and recovery variants run in the opt-in subprocess profile.
    "tests/test_auto_integrate_end_to_end.py",
    # Every merge-refusal shape (untracked / uncommitted obstructions) must
    # end with the branch containing its target -- the guard against
    # planning on a stale branch. Real git, so it is registered here.
    "tests/test_integration_obstruction_real_git.py",
    "tests/test_pending_merge_commit_recovery.py",
    "tests/test_pending_merge_repair_real_git.py",
    "tests/test_retained_resolution_handoff.py",
    "tests/test_retained_resolution_dispatch.py",
    "tests/integration/test_retained_integration_acceptance.py",
    # Real-Git regression for the workspace-bounded git cwd contract
    # (symlink and parent-repo bypass shapes). Must run under the default
    # ``make test`` profile so the boundary cannot rot silently.
    # U5: worktree-qualified backup-ref identity. Real-Git proof that
    # linked worktrees sharing a pre-feature SHA get distinct refs and
    # that one operation's cleanup never enumerates the namespace.
    "tests/test_backup_ref_operation_scope.py",
    "tests/test_tool_git_read_path_validation.py",
    # I1/I2/I4 housekeeping bounds live in a subprocess_e2e module; keep
    # them on the default make-test profile via this registry.
    "tests/test_commit_cleanup_invariants.py",
    "tests/test_commit_cleanup_verify_gate.py",
    "tests/integration/test_pipeline_commit_cleanup_to_commit_e2e.py",
    # wt-11: crash-safety and concurrency proofs for the indexed
    # exploration substrate (S-5). subprocess_e2e so the cross-process
    # boundary and atomic promotion are exercised; in-budget on the
    # 60s combined verify budget.
    "tests/test_explore_crash_safety.py",
    "tests/test_explore_concurrency.py",
    # wt-11 S-1: cold-start lifecycle regression (E1/E2/E3) is
    # subprocess_e2e (the stale-handle test must cross a process
    # boundary to be honest) and must stay on the default
    # make-test profile so the regression cannot rot silently.
    "tests/test_explore_cold_start_lifecycle.py",
    # wt-11 S-5: in-budget perf regression gate validates the
    # committed baseline JSON against the documented numeric
    # targets and the schema validator; the slow full-capture
    # layer lives in a dedicated subprocess_e2e test so this
    # file stays inside the 60s combined verify budget.
    "tests/test_explore_perf_regression_gate.py",
    # wt-12: the deterministic auto-commit acceptance cases for the
    # skill and policy surfaces are subprocess_e2e (real-git boundary
    # exercised end-to-end). Keep them on the default ``make test``
    # profile so the isolation / rollback contract cannot rot
    # silently. In-budget on the 60s combined verify budget.
    "tests/test_skills_auto_commit.py",
    # wt-12: the producer-level install + auto-commit boundary cases
    # (dir-to-symlink transition, byte-equal trap, prune +
    # sibling-symlink, unrelated-dirty-file guard, no-op second
    # run) are split from test_skills_auto_commit to keep the
    # shared-primitive test under the audit's 1000-line cap. The
    # split file runs in the same default make test profile so the
    # boundary regression coverage cannot rot silently.
    "tests/test_skills_install_auto_commit.py",
    "tests/project_policy/test_policy_auto_commit.py",
    # wt-12: the shared primitive's own real-git regression cases
    # (exact-path HEAD lookup, dir→symlink transition, FAILED
    # rollback, ancestor-symlink safety). Real-git boundary
    # required for the prefix-collision and dir→symlink shapes; the
    # default ``make test`` profile is the only way the contract
    # cannot rot silently.
    "tests/test_scoped_auto_commit.py",
    # wt-12: the deterministic auto-commit acceptance cases for the
    # config write sites (migration, autowire, enable, init starter
    # prompt). subprocess_e2e (real-git boundary exercised end-to-end)
    # but must stay on the default ``make test`` profile so the
    # config-write isolation / rollback contract cannot rot silently.
    # In-budget on the 60s combined verify budget.
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
    # wt-15-policy (U1): the parametrized display module was previously
    # 460 tests and wall-clock bottleneck; split into three coherent files
    # (catalog/surface, renderer/output, salience/idle-burst) to keep each
    # under the audit's 1000-line cap. All three files must remain on the
    # default make-test profile so the scene rendering contracts cannot rot.
    "tests/display/test_generated_scene_catalog.py",
    "tests/display/test_generated_scene_renderer.py",
    "tests/display/test_generated_scene_salience.py",
    # wt-03-fix-bug (S): the linked-worktree regression test for the
    # diagnostic-evidence collector. Real-git, in-budget, must run
    # on the default ``make test`` profile so the architecture's
    # prohibition of repository-global reads cannot rot silently.
    "tests/test_integration_diagnostic_worktree_isolation.py",
    "tests/agents/invoke/test_real_pty_completion_no_terminal_lost.py",
    # wt-03-fix-bug (WU-5): the package-wide ``live_tree`` regression
    # gates in the opts-key and kwargs-forwarding audits each scan
    # the full shipped tree. They are slow under a single pytest
    # worker (~6 s for opts-key drift, ~2.5 s for kwargs-forwarding)
    # and would tip a single-file shard past the 60 s combined
    # budget even when every other shard finishes inside it. Moving
    # them to the E2E shard gives the four in-shard xdist workers
    # (the host-invariant ``_REQUIRED_E2E_SHARD_XDIST_WORKERS`` cap)
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
    # MCP respawn bug (R1-R4, S1-S4).
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
    # wt-16 (WU-B follow-up): both files shell out to real git in tmp_path
    # repos and now carry the ``subprocess_e2e`` module marker (enforced by
    # the process-audit AST exemption), so they MUST be registered here to
    # stay on the default ``make test`` profile — otherwise discovery
    # excludes them and their coverage silently rots.
    "tests/test_display_lifecycle_label_taxonomy.py",
    "tests/test_unreadable_record_entry_points.py",
)
_VERIFICATION_MARK_EXPRESSION = "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e"
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
# DA-003 (wt-028-display P1 / AC-08 / S-13): when the 60s parent
# deadline fires, the runner has already given us its 60s budget;
# we only get back the budget tracker wall time if we exit
# within the remaining 0-5s of drain. The previous 5s drain
# routinely pushed the cumulative wall time over the immutable
# 60s budget even when the slowest shard was well under 60s.
# 1s is the lower bound that lets pytest print its summary line
# before SIGKILL; shorter drain values produce empty ``pytest shard
# did not exit after termination`` banners.
# wt-059 DA-005: the ``run_verify`` cumulative budget is CHARGED at the
# whole-step granularity: every tracked step must fit inside its own
# 60 s cap AND leave room for the other tracked steps. The multiplier
# was reduced from 120 to 60 when the 120x value starved unit-test
# shards; it was further reduced to 1 (no isolation) because the two
# REQUIRED files use per-test ``tmp_path`` fixtures and distribute
# across shards safely. Keeping any multiplier above 1 created a
# partition whose slowest shard exceeded the 50 s make-test step.
_SHARD_TERMINATION_DRAIN_SECONDS = 1.0
_REQUIRED_E2E_WEIGHT_MULTIPLIER = 1
#: Number of slowest tests reported by ``pytest --durations=N`` on each
#: shard. The runner always passes this flag so when a shard is killed
#: by the 60s combined budget, the captured stdout still names the
#: heaviest tests in that shard. The flag adds a sub-second timing
#: pass after the slowest call is recorded; the overhead is bounded
#: by the maintained test-suite size and is negligible relative to
#: the wall-clock saving it produces on the next bottleneck hunt.
#: Set to ``0`` to disable (no slowest-test reporting).
_PYTEST_DURATIONS_REPORT: int = 10


# Required real-git files use a dedicated shard with four xdist workers.
# wt-03-fix-bug: the cap is host-invariant: 4 workers on every host
# keep the total concurrent process count at 12 (8 general + 4 E2E)
# under high host load; E2E tests take ~1.25x longer per-worker but
# reduced contention lets general shards complete faster, keeping
# combined wall-clock inside the 60 s budget. The cumulative 60 s
# budget in ``ralph/verify.py`` is preserved.
def _required_e2e_xdist_workers() -> str:
    return "4"


_REQUIRED_E2E_SHARD_XDIST_WORKERS = _required_e2e_xdist_workers()
_PARAMETRIZE_CASES_ARGUMENT_INDEX = 1
_DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES: Mapping[str, int] = {
    "tests/display/test_generated_scene_catalog.py": 45,
    "tests/display/test_generated_scene_renderer.py": 40,
    "tests/display/test_generated_scene_salience.py": 15,
    "tests/test_brand_naming.py": 78,
    "tests/test_audit_kwargs_forwarding.py": 50,
    "tests/test_no_anti_drift_regression.py": 76,
    "tests/test_audit_opts_key_drift.py": 300,
    "tests/test_explore_bench_rss_isolation.py": 70,
    # Calibrated from --profile-durations per-file wall-clock on shard 15
    # (weight = round(measured_seconds * 17); band [50, 460] per existing
    # calibration, ~1022 shard weight / 60 s shard budget). These files sit
    # on the slowest shard and their heuristic test-count weight
    # underestimates their wall-clock cost.
    "tests/test_deterministic_writers_clean_tree.py": 133,
    "tests/test_audit_artifact_submission_canonical_path.py": 131,
    "tests/project_policy/test_run_integration.py": 60,
    "tests/agents/test_workspace_event_backpressure.py": 56,
    "tests/test_phases_retry_on_stale_session.py": 53,
    "tests/display/test_operator_display_labels.py": 52,
}


if not EXCLUSIVE_SUBPROCESS_E2E_FILES:
    raise RuntimeError("EXCLUSIVE_SUBPROCESS_E2E_FILES must not be empty")
if len(EXCLUSIVE_SUBPROCESS_E2E_FILES) != len(set(EXCLUSIVE_SUBPROCESS_E2E_FILES)):
    raise RuntimeError("EXCLUSIVE_SUBPROCESS_E2E_FILES must not contain duplicates")
if not REQUIRED_AUTO_INTEGRATE_E2E_FILES:
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not be empty")
if len(REQUIRED_AUTO_INTEGRATE_E2E_FILES) != len(set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)):
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not contain duplicates")
if _MAX_PYTEST_WORKERS <= 0:
    raise RuntimeError("_MAX_PYTEST_WORKERS must be positive")
if _MAX_XDIST_WORKERS_PER_SHARD < 0:
    raise RuntimeError("_MAX_XDIST_WORKERS_PER_SHARD must be non-negative")


def validate_exclusive_subprocess_e2e_selection(selected_files: Iterable[str]) -> None:
    """Fail unless each exclusive subprocess-E2E file is selected exactly once."""
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
    """Fail when combined pytest selection omits a required E2E file."""
    selected = frozenset(selected_files)
    missing = tuple(path for path in REQUIRED_AUTO_INTEGRATE_E2E_FILES if path not in selected)
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
    """Partition selected test files deterministically across workers.

    Required auto-integration E2E files and remaining files use the same
    deterministic largest-processing-time (LPT) placement. This isolates a
    heavy real-git file instead of round-robinning it onto an already loaded
    shard, minimizing the maximum predicted shard load. Exact-once assignment
    and deterministic weighting are preserved by sorting by weight then path.
    """
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

    # DA-001: narrow ``effective_weights`` from the ``Mapping[str, int] | None``
    # parameter type so the per-shard budget math below infers ``int``,
    # not ``Any``.
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
    """Fail unless shards contain every selected file exactly once."""
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
    """Return an explicit override or the CPU-capped verified shard profile.

    The auto profile is bounded by ``_PERFORMANCE_CORE_PYTEST_WORKER_CAP = 10``
    on 12-core hosts, and by ``_MAX_PYTEST_WORKERS = 8`` on larger hosts to avoid
    startup contention. Explicit overrides remain capped at ``available_cores - 2``
    to reserve the parent and OS/I/O capacity. The 12-core profile reserves two
    logical cores for orchestration.
    """
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
    """Return the in-shard xdist worker count, or ``"0"`` to disable xdist.

    The default is ``"0"`` (plain pytest per shard), which on the maintained
    8-shard CI profile keeps the slowest shard well under the 60-second
    combined budget because the shard-saturated 8-shard fan-out already
    uses one pytest process per shard and adding in-shard xdist workers
    trades parallel IO for pytest-coordination overhead. Operators
    on hosts with idle cores beyond the shard cap (e.g. 64-core CI), or
    hosts with very few shards, may opt into the legacy
    CPU-utilisation-maximising policy by setting
    ``PYTEST_XDIST_WORKERS_PER_SHARD=auto``; ``auto`` then sizes each
    shard's xdist fan-out so that ``PYTEST_WORKERS`` shards * ``N``
    in-shard workers does not exceed the host's available CPU count.
    Setting ``PYTEST_XDIST_WORKERS_PER_SHARD`` to a positive integer
    overrides the policy with an explicit per-shard fan-out; ``0`` opts
    out of in-shard xdist (the default).
    """
    raw = os.getenv("PYTEST_XDIST_WORKERS_PER_SHARD", _DEFAULT_XDIST_WORKERS_PER_SHARD)
    if raw != "auto":
        return raw
    available_cores = os.cpu_count() or 2
    # ``_pytest_workers`` already keeps one core for the runner's
    # deadline/cleanup loop, so the in-shard fan-out is bounded by the
    # remaining cores divided by the shard count. We cap each shard at
    # ``_MAX_XDIST_WORKERS_PER_SHARD`` so a future per-shard bump cannot
    # silently exceed the host's actual parallelism.
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
    """Build the pytest command for one shard.

    DA-003 (wt-028-display P1 / AC-08 / S-13): the shard
    command opts out of pytest-cache writes
    (``-p no:cacheprovider``) and header output
    (``--no-header``) so the per-shard overhead of writing the
    ``tests/.pytest_cache`` directory and emitting the platform/
    rootdir banner is bounded. On the maintained 8-shard fan-out the
    cache-write + banner path adds 100-300 ms of wall time per
    shard; eliminating it on every shard keeps the slowest shard
    well under the combined 60-second budget. The cache is
    still honored for raw targets that deliberately use the default
    xdist path, while maintained shard profiles avoid shared cache writes.

    wt-01-fs-opti S-1: the shard command additionally opts into pytest-xdist
    (``-n N`` + ``--dist loadgroup``) when ``xdist_workers`` is a positive
    integer. The in-shard xdist fan-out shrinks each shard's wall clock
    so the slowest shard fits inside the 60s combined budget enforced
    upstream by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``. The
    ``loadgroup`` scheduler keeps long-running tests on the same worker
    rather than scattering them, which is the right default for the
    maintenance-heavy integration tests. ``xdist_workers == "0"`` opts out
    of xdist (legacy plain-pytest behavior) and is reserved for an
    operator override.
    """
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
    """Return whether an expression is the ``pytest.mark.subprocess_e2e`` marker."""
    return (
        isinstance(expression, ast.Attribute)
        and expression.attr == "subprocess_e2e"
        and isinstance(expression.value, ast.Attribute)
        and expression.value.attr == "mark"
        and isinstance(expression.value.value, ast.Name)
        and expression.value.value.id == "pytest"
    )


def _is_module_subprocess_e2e(source: str, *, filename: str) -> bool:
    """Return whether a module-level ``pytestmark`` marks every item as E2E."""
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
            isinstance(statement, ast.AnnAssign)
            and isinstance(statement.target, ast.Name)
            and (statement.target.id == "pytestmark")
        ):
            # An annotation without a value or a non-simple later assignment
            # makes static classification uncertain, so retain the module.
            final_marker = None
        elif (
            isinstance(statement, ast.AugAssign)
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
    """Return all pytest files using pytest's default filename patterns."""
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
    """Return a focused profile's static file selection without pytest collection."""
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
    """Return unit files, excluding the integration subtree exactly."""
    return _discover_profile_test_files(cwd, integration_only=False)


def discover_integration_test_files(cwd: Path) -> tuple[str, ...]:
    """Return integration files from the integration subtree exactly."""
    return _discover_profile_test_files(cwd, integration_only=True)


def discover_fast_test_files(cwd: Path) -> tuple[str, ...]:
    """Return the fail-closed static test-fast routing-contract selection."""
    missing_files = tuple(path for path in FAST_PROFILE_TEST_FILES if not (cwd / path).is_file())
    if missing_files:
        raise RuntimeError(f"static fast pytest discovery missing files: {missing_files}")
    return FAST_PROFILE_TEST_FILES


def discover_test_files(cwd: Path) -> tuple[str, ...]:
    """Return verification files, excluding only statically proven E2E-only modules.

    Avoid parsing the common non-E2E case: a module can only be E2E-only when
    it assigns ``pytestmark`` and mentions ``subprocess_e2e``.  This keeps
    discovery below the one-second per-test ceiling while preserving the
    conservative AST classification for every possible E2E candidate.

    ``_FILE_SOURCE_CACHE`` so ``_test_file_weight`` does not re-read the same
    file from disk during shard weight computation. Per-file weights are
    also populated into ``_FILE_WEIGHT_CACHE`` during the same single pass:
    the common non-E2E path uses a lightweight regex over the source to
    count ``test_`` function definitions (parametrize multipliers are not
    material to LPT placement), while files already requiring
    ``ast.parse`` for E2E classification get an exact AST-derived weight
    for free. On a 1300-file tree this saves ~5s of redundant ``ast.parse``
    work in the parent process, which directly lowers the slowest-shard
    wall time under the 60s combined budget. Required auto-integrate E2E
    files also cache their source; the multiplier is applied once by
    ``_test_file_weight`` when the shard plan is built.
    """
    discovered: list[str] = []
    for path in _discover_all_test_files(cwd):
        if path in REQUIRED_AUTO_INTEGRATE_E2E_FILES:
            source = (cwd / path).read_text(encoding="utf-8")
            _cache_source(path, source)
            _FILE_WEIGHT_CACHE[path] = estimate_test_file_weight(source)
            discovered.append(path)
            continue
        source_bytes = (cwd / path).read_bytes()
        if b"pytestmark" not in source_bytes or b"subprocess_e2e" not in source_bytes:
            source = source_bytes.decode("utf-8")
            _cache_source(path, source)
            _FILE_WEIGHT_CACHE[path] = _DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES.get(
                path
            ) or _fast_test_count(source)
            discovered.append(path)
            continue
        source = source_bytes.decode("utf-8")
        if not _is_module_subprocess_e2e(source, filename=path):
            _cache_source(path, source)
            _FILE_WEIGHT_CACHE[path] = _DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES.get(
                path
            ) or estimate_test_file_weight(source)
            discovered.append(path)
    selected = tuple(discovered)
    validate_required_auto_integrate_selection(selected)
    return selected


def discover_subprocess_e2e_files(cwd: Path) -> tuple[str, ...]:
    """Return test files that statically reference the subprocess-E2E marker.

    Supplying explicit files avoids pytest collecting the entire repository
    merely to deselect non-E2E tests. Pytest still applies the canonical marker
    expression within each selected file, preserving function-level exclusions.

    The maintained 1300-file tree previously spent ~7.5 s in this pass
    parsing every file even when most files contain no ``subprocess_e2e``
    marker. A two-stage filter avoids that work:

    1. Skip files that don't even contain the ``subprocess_e2e`` substring
       (cheap ``bytes`` membership test).
    2. Stop walking the AST as soon as one ``pytest.mark.subprocess_e2e``
       reference is found (early exit from ``ast.walk``).
    """
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
    """Return True iff ``tree`` contains at least one ``pytest.mark.subprocess_e2e`` reference."""
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
    """Return the statically knowable collection multiplier for one decorator."""
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
    """Estimate collection work from visible tests and literal parametrization.

    Literal ``pytest.mark.parametrize`` case lists represent distinct pytest
    items, so account for them rather than balancing only function definitions.
    Dynamic parameter sources remain conservatively weighted as one item.

    pytest only collects test functions at module scope or as direct methods
    of a class. This walker descends through ``ast.ClassDef`` bodies but
    stops at any function (whether it is a test or not) because pytest does
    not collect test functions nested inside other functions. Skipping
    nested functions and the rest of ``ast.walk`` keeps the walk below
    4.5 ms per file across a 1300-file tree, halving the original
    ``ast.walk``-based budget without changing the reported weight for
    any file in the maintained suite (verified via the equivalence test in
    ``tests/test_test_suites.py::test_estimate_test_file_weight_matches_ast_walk``).
    """
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
    """Count test definitions and literal parametrized collection cases.

    This AST-free estimate is used during static discovery for LPT shard
    placement. A decorator's literal case list is multiplied into the
    immediately following test definition, preventing large parametrized
    modules from being assigned a deceptively small weight.
    """
    if "parametrize" in source:
        return estimate_test_file_weight(source)
    return sum(1 for line in source.splitlines() if _TEST_DEF_PATTERN.match(line))


def _test_file_weight(cwd: Path, relative_path: str) -> int:
    override = _DYNAMIC_TEST_FILE_WEIGHT_OVERRIDES.get(relative_path)
    if override is not None:
        return override
    # Reuse the parser cache populated by ``discover_test_files`` to avoid
    # re-reading and re-parsing the same source files during shard weight
    # computation. Without this cache, each shard load pays the cost of an
    # additional full read and ``ast.parse`` for every selected file even
    # though ``discover_test_files`` already walked the same files.
    weight = _estimate_weight_from_cache(relative_path)
    if weight is None:
        source = (cwd / relative_path).read_text(encoding="utf-8")
        weight = estimate_test_file_weight(source)
    if relative_path in REQUIRED_AUTO_INTEGRATE_E2E_FILES:
        return weight * _REQUIRED_E2E_WEIGHT_MULTIPLIER
    return weight


_FILE_SOURCE_CACHE: dict[str, str] = {}
_FILE_WEIGHT_CACHE: dict[str, int] = {}


def _cache_source(path: str, source: str) -> None:
    """Store the parsed source for ``path`` so later weight lookups avoid re-reading."""
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
    """Reset the transient source/weight caches populated during file discovery.

    The runner calls this between separate ``make test`` / ``make
    test-subprocess-e2e`` invocations so a stale cache cannot leak across
    pytest profiles; the in-flight invocation still benefits from the
    cached source the discovery pass already paid to parse.
    """
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
    # Each ``process.terminate`` is wrapped so a raise on one process
    # (e.g. a foreign exception from a real process manager when its
    # descendant accounting trips) cannot skip the rest of the live
    # shard pool. The original failure is observed (a stderr line)
    # while every still-running shard is still reaped; the budget
    # tracker in ``ralph/verify.py`` charges this drain, so
    # short-cutting one terminate call leaks the rest of the
    # process group and inflates wall-clock on a busy host.
    for process in processes:
        if process.poll() is None:
            try:
                process.terminate(grace_period_s=0.0)
            except Exception as exc:
                # Best-effort reap: a foreign raise from a real process
                # manager cannot skip the rest of the live shard pool.
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
    """Name each incomplete shard before deadline cleanup loses its pytest output."""
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
    required_e2e_shard_xdist_workers: str | None = None,
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
            shard_xdist_workers = xdist_workers
            if required_e2e_shard_xdist_workers is not None and shard_index == len(shards) - 1:
                shard_xdist_workers = required_e2e_shard_xdist_workers
            manifest_path = basetemp_root / f"shard-{shard_index}.manifest"
            manifest_path.write_text(  # filesystem-write-ok: transient per-run shard scratch
                "".join(f"{path}\n" for path in shard),
                encoding="utf-8",
            )
            print(
                f"pytest shard {shard_index}: {len(shard)} files, "
                f"xdist={shard_xdist_workers}, started at {monotonic() - started_at:.2f}s",
                flush=True,
            )
            processes.append(
                spawner(
                    _shard_command(
                        manifest_path=manifest_path,
                        basetemp=basetemp_root / f"shard-{shard_index}",
                        marker_expression=marker_expression,
                        xdist_workers=shard_xdist_workers,
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
) -> tuple[tuple[str, ...], str, tuple[str, ...], str | None]:
    """Resolve one fail-closed profile into static files and shard settings."""
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
        return (REQUIRED_AUTO_INTEGRATE_E2E_FILES, _VERIFICATION_MARK_EXPRESSION, (), None)
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
            "0",
        )
    discovered_files = file_discoverer(cwd)
    validate_required_auto_integrate_selection(discovered_files)
    required_files = tuple(
        path for path in discovered_files if path in set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    )
    selected_files = tuple(
        path for path in discovered_files if path not in set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    )
    required_workers = _REQUIRED_E2E_SHARD_XDIST_WORKERS if required_files else None
    return (selected_files, _VERIFICATION_MARK_EXPRESSION, required_files, required_workers)


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
    """Run the maintained pytest verification suite and return its exit code.

    Args:
        cwd: Working directory for the pytest subprocess (the package
            root that contains ``tests/``).
        suite_timeout_seconds: Wall-clock cap for this single pytest
            invocation. Default ``DEFAULT_SUITE_TIMEOUT_SECONDS``
            (60 s). Note this is a per-invocation cap only — the
            60-second COMBINED budget across every test step is
            enforced upstream by ``ralph.verify`` via cumulative
            ``time.monotonic()`` tracking; the elapsed time of this
            function counts against that budget.
        spawner: Process seam used to start each plain-pytest shard.
        file_discoverer: Static pytest-file discovery seam.
        file_weigher: Static deterministic load weight for each selected file.

    Returns:
        ``0`` on success, the first failing pytest shard's return code, or
        ``124`` when the single parent deadline expires.

    Side effects:
        Spawns concurrent pytest shards via ``spawner``. Each shard
        inherits an environment with
        ``RALPH_PYTEST_TEST_TIMEOUT_SECONDS`` and
        ``RALPH_PYTEST_SUITE_TIMEOUT_SECONDS`` populated.
    """
    started_at = monotonic()
    deadline = started_at + suite_timeout_seconds
    inherited_env = dict(os.environ)
    # An activated external environment can export site-packages through
    # PYTHONPATH. Pytest shards must use the same interpreter selected by
    # ``sys.executable`` rather than importing incompatible binary wheels.
    # The Makefile deliberately exports this package root, however, so retain
    # that exact, single-entry value: it prevents Python from falling back to
    # packages compiled for an unrelated interpreter.
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
        required_e2e_shard_xdist_workers,
    ) = _select_test_files(
        cwd=cwd,
        file_discoverer=file_discoverer,
        profile=profile,
        auto_integrate_e2e_only=auto_integrate_e2e_only,
        subprocess_e2e_only=subprocess_e2e_only,
    )

    # These files used to run in a pytest process that had to exit before
    # the parallel fan-out started. That process spent ~25s (full-tree
    # collection plus ~11s of fast tests) on the critical path, so the
    # parallel shards started with too little of the 60s combined budget
    # left and the suite watchdog killed them. The product-baseline bench
    # stays under 1s even under xdist load, so the files join the same
    # LPT partition as every other selected file.
    shards = partition_selected_files(
        selected_files,
        worker_count=int(_pytest_workers()),
        file_weights={path: file_weigher(cwd, path) for path in selected_files},
    )
    if required_e2e_shard:
        validate_exact_file_assignment(
            (*selected_files, *required_e2e_shard), (*shards, required_e2e_shard)
        )
    else:
        validate_exact_file_assignment(selected_files, shards)
    print(
        f"pytest preparation: {len(selected_files) + len(required_e2e_shard)} files, "
        f"{len(shards) + bool(required_e2e_shard)} shards in {monotonic() - started_at:.2f}s",
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
        # The real-git E2E shard runs concurrently with the general
        # shards using dedicated xdist workers so all shards complete
        # inside the 60 s combined budget in ralph/verify.py. The exclusive
        # subprocess_e2e lane continues to sequence after general shards.
        run_required_concurrently = bool(required_e2e_shard and not subprocess_e2e_only)
        all_shards = (*shards, required_e2e_shard) if run_required_concurrently else shards
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
            required_e2e_shard_xdist_workers=(
                required_e2e_shard_xdist_workers if run_required_concurrently else None
            ),
            successful_returncodes=successful_returncodes,
            empty_selection_returncode=empty_selection_returncode,
        )
        if general_result not in successful_returncodes:
            return general_result
        if not required_e2e_shard or run_required_concurrently:
            return general_result
        return _run_shards(
            (required_e2e_shard,),
            cwd=cwd,
            env=env,
            basetemp_root=basetemp_path,
            deadline=deadline,
            started_at=started_at,
            spawner=spawner,
            monotonic=monotonic,
            wait=wait,
            marker_expression=marker_expression,
            xdist_workers=required_e2e_shard_xdist_workers or _xdist_workers_per_shard(),
            successful_returncodes=successful_returncodes,
            empty_selection_returncode=empty_selection_returncode,
        )


def _run_durations_profile(cwd: Path) -> int:
    """Run each test file individually and print per-file wall-clock durations."""
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
    """Entry point for the ``ralph.test_suites`` command-line tool.

    Forwards to :func:`run_test_suites` using the current working
    directory. Returns the pytest subprocess exit code. Positional
    arguments are rejected with ``SystemExit`` to surface silent
    misuse.
    """
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
