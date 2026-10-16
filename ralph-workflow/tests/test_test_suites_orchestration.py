"""Orchestration, worker-count, and timeout tests for the test-suite runner.

Split out of :mod:`tests.test_test_suites` so the shared suite stays under
the maintained 1000-line-per-file cap. The shard fake classes live in
:mod:`tests._test_test_suites_helpers` to keep both this file and the
discovery suite free of repeated fakes.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import test_suites as test_suites_module
from tests._test_test_suites_helpers import (
    _FakeClock,
    _FakeShardProcess,
    _StubSpawner,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES: tuple[str, ...] = (
    "tests/test_auto_integrate_end_to_end.py",
    "tests/test_integration_obstruction_real_git.py",
    "tests/test_pending_merge_commit_recovery.py",
    "tests/test_pending_merge_repair_real_git.py",
    "tests/test_retained_resolution_handoff.py",
    "tests/test_retained_resolution_dispatch.py",
    "tests/integration/test_retained_integration_acceptance.py",
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
    "tests/test_display_generated_scenes.py",
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
    # wt-16 (WU-B follow-up): both files shell out to real git in tmp_path
    # repos and now carry the ``subprocess_e2e`` module marker (enforced by
    # the process-audit AST exemption), so they MUST be registered here to
    # stay on the default ``make test`` profile — otherwise discovery
    # excludes them and their coverage silently rots.
    "tests/test_display_lifecycle_label_taxonomy.py",
    "tests/test_unreadable_record_entry_points.py",
)


def test_pytest_shard_processes_disable_background_reaping_and_event_logging() -> None:
    """Shard teardown owns lifecycle cleanup without per-shard background work."""
    policy = test_suites_module._PYTEST_SHARD_PROCESS_MANAGER.policy

    assert policy.log_events is False
    assert policy.enable_zombie_reaper is False


def test_default_spawner_inherits_parent_streams_for_verifier_log_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INFO-level Loguru output must not fill one independently polled shard pipe."""
    captured_options: list[object] = []
    sentinel = object()

    def capture_spawn(_command: Sequence[str], options: object) -> object:
        captured_options.append(options)
        return sentinel

    monkeypatch.setattr(test_suites_module._PYTEST_SHARD_PROCESS_MANAGER, "spawn", capture_spawn)

    assert (
        test_suites_module._default_spawner(
            ("pytest",),
            cwd=tmp_path,
            env={},
        )
        is sentinel
    )

    assert len(captured_options) == 1
    options = captured_options[0]
    assert options.stdout is None
    assert options.stderr is None
    assert options.stdout != subprocess.PIPE
    assert options.stderr != subprocess.PIPE


@pytest.mark.parametrize(
    ("cpu_count", "expected_workers"),
    (
        (None, "1"),
        (1, "1"),
        (2, "1"),
        (12, "10"),
        (16, "8"),
        (32, "8"),
        (64, "8"),
    ),
)
def test_auto_worker_count_uses_verified_sixteen_shard_cap(
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    expected_workers: str,
) -> None:
    """Auto profile caps shards at the verified eight-worker limit.

    Eight shards keep the maintained 40-core host's full selected suite
    inside the immutable 60 s combined budget while preserving
    exact-once selection. Sixteen shards caused I/O contention during
    collection, timing out every shard on the 40-core host. The 12-core
    profile reserves two logical cores for orchestration while using the
    other ten for test shards.
    """
    monkeypatch.delenv("PYTEST_WORKERS", raising=False)
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)

    assert test_suites_module._pytest_workers() == expected_workers


def test_explicit_worker_count_overrides_the_auto_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "3")

    assert test_suites_module._pytest_workers() == "3"


@pytest.mark.parametrize(
    ("cpu_count", "requested", "expected"),
    (
        (12, "12", "10"),
        (12, "32", "10"),
        (8, "12", "6"),
        (32, "12", "12"),
        (4, "3", "2"),
    ),
)
def test_explicit_worker_count_capped_at_cores_minus_two(
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int,
    requested: str,
    expected: str,
) -> None:
    """Explicit PYTEST_WORKERS is capped at available_cores - 2.

    One core is reserved for the parent process (shard polling, SIGCHLD
    cleanup) and one for OS / I/O overhead. On a 12-core host, the Makefile's
    twelve-shard default is capped to ten effective shards; on
    a 32-core CI host 12 is preserved unchanged. Explicit overrides retain
    their documented cap.
    """
    monkeypatch.setenv("PYTEST_WORKERS", requested)
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)

    assert test_suites_module._pytest_workers() == expected


@pytest.mark.parametrize(
    ("cpu_count", "shard_count"),
    (
        (None, "1"),
        (1, "1"),
        (2, "1"),
        (16, "15"),
        (32, "16"),
        (64, "16"),
    ),
)
def test_default_xdist_workers_per_shard_is_zero(
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    shard_count: str,
) -> None:
    """Default in-shard xdist is disabled on every host profile.

    The plain-pytest per-shard path keeps the slowest shard under the
    combined 60-second budget on the maintained 32-core CI profile
    because the shard-saturated 16-shard fan-out already maximises
    pytest-process parallelism and adding in-shard xdist workers only
    shifts wall-clock budget from parallel IO into pytest-coordination
    overhead. Operators that want the legacy CPU-utilisation-maximising
    policy must opt in by setting ``PYTEST_XDIST_WORKERS_PER_SHARD=auto``
    (see ``test_auto_xdist_worker_count_respects_available_cores``).
    """
    monkeypatch.delenv("PYTEST_XDIST_WORKERS_PER_SHARD", raising=False)
    monkeypatch.setenv("PYTEST_WORKERS", shard_count)
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)

    assert test_suites_module._xdist_workers_per_shard() == "0"


@pytest.mark.parametrize(
    ("cpu_count", "shard_count", "expected_workers"),
    (
        (None, "1", "2"),
        (1, "1", "1"),
        (2, "1", "2"),
        (16, "15", "1"),
        (32, "16", "2"),
        (64, "16", "4"),
    ),
)
def test_auto_xdist_worker_count_respects_available_cores(
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    shard_count: str,
    expected_workers: str,
) -> None:
    """``PYTEST_XDIST_WORKERS_PER_SHARD=auto`` sizes in-shard fan-out from cores.

    With the env var explicitly set to ``"auto"`` the resolver sizes the
    in-shard xdist fan-out so that ``PYTEST_WORKERS`` shards * ``N``
    in-shard workers does not exceed the host's available CPU count, up
    to ``_MAX_XDIST_WORKERS_PER_SHARD``. This is the legacy
    CPU-utilisation-maximising policy and is no longer the default; see
    ``test_default_xdist_workers_per_shard_is_zero`` for the rationale.
    """
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "auto")
    monkeypatch.setenv("PYTEST_WORKERS", shard_count)
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)

    assert test_suites_module._xdist_workers_per_shard() == expected_workers


def test_explicit_xdist_worker_count_overrides_auto_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "3")

    assert test_suites_module._xdist_workers_per_shard() == "3"


def test_run_test_suites_runs_disjoint_plain_pytest_shards(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "0")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    processes = [
        _FakeShardProcess([0], stdout=b"alpha passed\n"),
        _FakeShardProcess([0], stdout=b"bravo passed\n"),
    ]
    spawner = _StubSpawner(processes)
    clock = _FakeClock()

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        monotonic=clock,
        file_discoverer=lambda _cwd: (
            "tests/test_bravo.py",
            "tests/test_alpha.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "pytest preparation: 2 files, 2 shards in 0.00s" in output
    assert "pytest shard 0: 1 files, xdist=0, started at 0.00s" in output
    assert "pytest shard 1: 1 files, xdist=0, started at 0.00s" in output
    assert "pytest shard 0: exit=0, elapsed=0.00s" in output
    assert "pytest shard 1: exit=0, elapsed=0.00s" in output
    assert [command[3] for command, _cwd, _env in spawner.calls] == ["tests", "tests"]
    assert spawner.manifest_files == [
        ("tests/test_alpha.py",),
        ("tests/test_bravo.py",),
    ]
    assert all("-n" not in call[0] and "--dist" not in call[0] for call in spawner.calls)
    assert all(process.reaped for process in processes)
    env = spawner.calls[0][2]
    assert env[test_suites_module.TEST_TIMEOUT_ENV] == str(
        test_suites_module.timeout_seconds_from_env(
            test_suites_module.TEST_TIMEOUT_ENV,
            test_suites_module.DEFAULT_TEST_TIMEOUT_SECONDS,
        )
    )
    assert env["RALPH_PYTEST_SUITE_TIMEOUT_SECONDS"] == str(
        test_suites_module.DEFAULT_SUITE_TIMEOUT_SECONDS
    )
    assert "RALPH_VERIFY_REQUIRED_AUTO_INTEGRATE_E2E" not in env


def test_scheduler_hypothesis_files_join_the_parallel_partition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    spawner = _StubSpawner([_FakeShardProcess([0]), _FakeShardProcess([0])])
    selected = (
        "tests/test_alpha.py",
        "tests/test_auto_integrate_boundary_cleanliness.py",
        "tests/test_bootstrap_auto_seed_gitignore.py",
        "tests/test_explore_fault_matrix_full.py",
        "tests/test_explore_handlers.py",
        "tests/test_scheduler.py",
        "tests/test_tool_git_read_handle_git_status.py",
    )

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: selected,
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    assigned = {path for manifest in spawner.manifest_files for path in manifest}
    assert assigned == set(selected)
    assert len(spawner.manifest_files) == 2
    assert len(spawner.calls) == 2


def test_run_test_suites_preserves_only_its_own_project_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    spawner = _StubSpawner([_FakeShardProcess([0])])

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            spawner=spawner,
            file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
        )
        == 0
    )
    assert spawner.calls[0][2]["PYTHONPATH"] == str(tmp_path)


def test_run_test_suites_does_not_leak_another_environment_pythonpath(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setenv("PYTHONPATH", "/other/environment/site-packages")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    spawner = _StubSpawner([_FakeShardProcess([0])])

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            spawner=spawner,
            file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
        )
        == 0
    )
    assert "PYTHONPATH" not in spawner.calls[0][2]


def test_pytest_tmpdir_regression_shards_use_isolated_repo_basetemps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each shard bypasses pytest's shared numbered temporary root."""
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    spawner = _StubSpawner([_FakeShardProcess([0]), _FakeShardProcess([0])])

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: (
            "tests/test_alpha.py",
            "tests/test_bravo.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    basetemps = tuple(
        Path(command[command.index("--basetemp") + 1]) for command, _cwd, _env in spawner.calls
    )
    assert exit_code == 0
    assert len(set(basetemps)) == 2
    assert {path.name for path in basetemps} == {"shard-0", "shard-1"}
    expected_parent = Path(tempfile.gettempdir()) / "ralph-pytest-shards"
    assert all(path.is_relative_to(expected_parent) for path in basetemps)
    assert not any(path.parent.exists() for path in basetemps)


def test_required_auto_integrate_e2e_registry_matches_verification_contract() -> None:
    assert (
        test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES
        == EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )
    assert len(set(test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES)) == len(
        EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )


def test_required_auto_integrate_selection_fails_closed_when_file_is_missing() -> None:
    selected = EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES[:-1]

    try:
        test_suites_module.validate_required_auto_integrate_selection(selected)
    except RuntimeError as exc:
        assert EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES[-1] in str(exc)
    else:
        raise AssertionError("missing required auto-integrate file was accepted")


def test_required_auto_integrate_selection_accepts_complete_registry() -> None:
    test_suites_module.validate_required_auto_integrate_selection(
        EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )


def test_focused_auto_integrate_profile_shards_exact_registry_without_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    processes = [_FakeShardProcess([0]), _FakeShardProcess([0])]
    spawner = _StubSpawner(processes)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
        auto_integrate_e2e_only=True,
    )

    assigned_files = tuple(path for shard in spawner.manifest_files for path in shard)
    assert exit_code == 0
    assert all(command[3] == "tests" for command, _cwd, _env in spawner.calls)
    assert sorted(assigned_files) == sorted(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    assert len(assigned_files) == len(set(assigned_files))


def test_subprocess_e2e_profile_uses_canonical_marker_with_explicit_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    spawner = _StubSpawner([_FakeShardProcess([0]), _FakeShardProcess([0])])
    monkeypatch.setattr(
        test_suites_module,
        "discover_subprocess_e2e_files",
        lambda _cwd: (
            "tests/test_e2e.py",
            "tests/agents/test_terminal_state_restored_on_exit.py",
        ),
    )
    weighed_paths: list[str] = []

    def weigh_file(_cwd: Path, path: str) -> int:
        weighed_paths.append(path)
        return 1

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            spawner=spawner,
            file_weigher=weigh_file,
            wait=lambda _seconds: None,
            subprocess_e2e_only=True,
        )
        == 0
    )
    assert weighed_paths == ["tests/test_e2e.py"]
    general_command = spawner.calls[0][0]
    exclusive_command = spawner.calls[1][0]
    assert general_command[3] == "tests"
    assert exclusive_command[3] == "tests"
    assert spawner.manifest_files == [
        ("tests/test_e2e.py",),
        ("tests/agents/test_terminal_state_restored_on_exit.py",),
    ]
    marker_flag = general_command.index("-m", general_command.index("pytest") + 1)
    assert general_command[marker_flag + 1] == test_suites_module._SUBPROCESS_E2E_MARK_EXPRESSION
    assert "-n" not in exclusive_command


@pytest.mark.parametrize(
    ("profile", "expected_files", "expected_marker"),
    (
        (
            "unit",
            ("tests/test_alpha.py",),
            "not subprocess_e2e and not smoke",
        ),
        (
            "integration",
            ("tests/integration/test_bravo.py",),
            "not subprocess_e2e and not smoke",
        ),
        (
            "fast",
            (
                "tests/test_makefile_verification_workflow.py",
                "tests/test_test_suites.py",
                "tests/test_test_suites_orchestration.py",
            ),
            "not subprocess_e2e and not smoke",
        ),
    ),
)
def test_run_test_suites_regression_profile_uses_exact_static_files_and_marker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    expected_files: tuple[str, ...],
    expected_marker: str,
) -> None:
    """S-2: named focused profiles retain exclusive paths and canonical markers."""
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    spawner = _StubSpawner([_FakeShardProcess([0])])
    if profile == "unit":
        discoverer = test_suites_module.discover_unit_test_files
        monkeypatch.setattr(test_suites_module, discoverer.__name__, lambda _cwd: expected_files)
    elif profile == "integration":
        discoverer = test_suites_module.discover_integration_test_files
        monkeypatch.setattr(test_suites_module, discoverer.__name__, lambda _cwd: expected_files)
    else:
        monkeypatch.setattr(
            test_suites_module,
            "discover_fast_test_files",
            lambda _cwd: expected_files,
        )

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            profile=profile,
            spawner=spawner,
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
        )
        == 0
    )
    assert spawner.manifest_files == [expected_files]
    command = spawner.calls[0][0]
    marker_flag = command.index("-m", command.index("pytest") + 1)
    assert command[marker_flag + 1] == expected_marker


def test_run_test_suites_regression_accepts_marker_empty_profile_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-2: a statically assigned file with no matching marker is not a profile failure."""
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "discover_unit_test_files",
        lambda _cwd: (
            "tests/test_empty.py",
            "tests/test_selected.py",
        ),
    )
    processes = [_FakeShardProcess([5]), _FakeShardProcess([0])]

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            profile="unit",
            spawner=_StubSpawner(processes),
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
        )
        == 0
    )
    assert all(process.reaped and not process.terminated for process in processes)


@pytest.mark.parametrize("shard_count", (1, 2))
def test_run_test_suites_regression_returns_no_tests_code_when_every_profile_shard_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    shard_count: int,
) -> None:
    """S-2: all marker-empty focused shards preserve pytest's no-tests exit code."""
    monkeypatch.setenv("PYTEST_WORKERS", str(shard_count))
    selected_files = tuple(f"tests/test_empty_{index}.py" for index in range(shard_count))
    monkeypatch.setattr(test_suites_module, "discover_unit_test_files", lambda _cwd: selected_files)
    processes = [_FakeShardProcess([5]) for _index in range(shard_count)]

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            profile="unit",
            spawner=_StubSpawner(processes),
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
        )
        == 5
    )
    assert all(process.reaped and not process.terminated for process in processes)


def test_run_test_suites_regression_rejects_conflicting_profile_selectors(tmp_path: Path) -> None:
    """S-2: callers cannot silently combine focused profile selection modes."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            profile="unit",
            auto_integrate_e2e_only=True,
        )


def test_main_regression_rejects_unknown_profile_before_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-2: unsupported CLI profiles fail closed instead of falling back to verification."""
    monkeypatch.setattr(
        test_suites_module,
        "run_test_suites",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("runner started")),
    )

    with pytest.raises(SystemExit, match="--profile durations"):
        test_suites_module.main(("--profile", "unknown"))


def test_default_profile_dedicated_required_e2e_shard_uses_four_xdist_workers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dedicated E2E shard uses four workers for isolated real-git tests.

    wt-03-fix-bug: reduced from 8 to 4 to cut total concurrent process
    count from 16 to 12 (8 general + 4 E2E) under high host load;
    E2E tests take ~1.25x longer per-worker but reduced contention
    lets general shards complete faster, keeping the combined wall-clock
    inside the 60 s budget. The 4-worker value was previously used
    and was increased to 8 to match general shard count on 40-core
    host; reduced back to 4 as part of the subprocess_e2e removal
    from required E2E files, which reduces the E2E shard workload.
    """
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    reap_spawn_counts: list[int] = []
    spawner = _StubSpawner([])
    processes = [
        _FakeShardProcess(
            [0],
            on_communicate=lambda: reap_spawn_counts.append(len(spawner.calls)),
        )
        for _index in range(3)
    ]
    spawner._processes.extend(processes)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: (
            *EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES,
            "tests/test_alpha.py",
            "tests/test_bravo.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    # Concurrent execution: all 3 shards (2 general + 1 required E2E)
    # are spawned concurrently in one wave, so reap_spawn_counts records
    # 3 at each reap.
    assert reap_spawn_counts == [3, 3, 3]
    dedicated_command = spawner.calls[-1][0]
    assert dedicated_command[3] == "tests"
    assert spawner.manifest_files[-1] == EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    xdist_index = dedicated_command.index("-n")
    assert dedicated_command[xdist_index + 1] == "4"
    assert dedicated_command[dedicated_command.index("--dist") + 1] == "loadgroup"


def test_subprocess_e2e_regression_general_failure_prevents_exclusive_pty_spawn(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S-1: the exclusive lane preserves first-failure output and cleanup semantics."""
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    failed = _FakeShardProcess([1], stderr=b"general failure output\\n")
    exclusive = _FakeShardProcess([0])
    spawner = _StubSpawner([failed, exclusive])
    monkeypatch.setattr(
        test_suites_module,
        "discover_subprocess_e2e_files",
        lambda _cwd: (
            "tests/test_general.py",
            "tests/agents/test_terminal_state_restored_on_exit.py",
        ),
    )

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            spawner=spawner,
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
            subprocess_e2e_only=True,
        )
        == 1
    )

    captured = capsys.readouterr()
    assert spawner.manifest_files == [("tests/test_general.py",)]
    assert failed.reaped and failed.orphans_cleaned
    assert not exclusive.reaped
    assert "general failure output" in captured.err


def test_subprocess_e2e_regression_exclusive_pty_uses_remaining_parent_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S-1: the PTY lane cannot reset the deadline consumed by general shards."""
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    clock = _FakeClock()
    general = _FakeShardProcess([0], on_communicate=lambda: clock.advance(5.0))
    exclusive = _FakeShardProcess([None])
    spawner = _StubSpawner([general, exclusive])
    monkeypatch.setattr(
        test_suites_module,
        "discover_subprocess_e2e_files",
        lambda _cwd: (
            "tests/test_general.py",
            "tests/agents/test_terminal_state_restored_on_exit.py",
        ),
    )

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            suite_timeout_seconds=5.0,
            spawner=spawner,
            file_weigher=lambda _cwd, _path: 1,
            monotonic=clock,
            wait=clock.advance,
            subprocess_e2e_only=True,
        )
        == 124
    )

    assert spawner.manifest_files == [("tests/test_general.py",)]
    assert general.reaped and general.orphans_cleaned
    assert not exclusive.reaped


def test_run_test_suites_terminates_and_reaps_siblings_on_first_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    failed = _FakeShardProcess([1], stderr=b"failed\n")
    sibling = _FakeShardProcess([None])
    spawner = _StubSpawner([failed, sibling])

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: (
            "tests/test_alpha.py",
            "tests/test_bravo.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 1
    assert failed.reaped
    assert not failed.terminated
    assert sibling.terminated
    assert sibling.reaped


def test_run_test_suites_uses_one_parent_deadline_and_reaps_all_on_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    processes = [_FakeShardProcess([None]), _FakeShardProcess([None])]
    spawner = _StubSpawner(processes)
    clock = _FakeClock()

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=spawner,
        file_discoverer=lambda _cwd: (
            "tests/test_alpha.py",
            "tests/test_bravo.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert all(process.terminated and process.reaped for process in processes)


def test_run_test_suites_spawn_deadline_names_already_started_shard(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    clock = _FakeClock()
    process = _FakeShardProcess([None])

    def spawn_after_deadline(
        command: Sequence[str], *, cwd: Path, env: Mapping[str, str]
    ) -> _FakeShardProcess:
        del command, cwd, env
        clock.advance(5.0)
        return process

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=spawn_after_deadline,
        file_discoverer=lambda _cwd: ("tests/test_alpha.py", "tests/test_bravo.py"),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert process.terminated and process.reaped
    assert capsys.readouterr().err.splitlines()[:1] == [
        "pytest shard 0 timed out after 5.00s; files: tests/test_alpha.py",
    ]


def test_run_test_suites_timeout_names_each_incomplete_shard_and_its_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    clock = _FakeClock()

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=_StubSpawner([_FakeShardProcess([None]), _FakeShardProcess([None])]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py", "tests/test_bravo.py"),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert capsys.readouterr().err.splitlines()[:2] == [
        "pytest shard 0 timed out after 5.00s; files: tests/test_alpha.py",
        "pytest shard 1 timed out after 5.00s; files: tests/test_bravo.py",
    ]


def test_timeout_regression_deadline_without_drain_does_not_claim_shard_survived(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A zero-budget deadline cannot honestly diagnose a failed post-termination drain."""
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    clock = _FakeClock()
    process = _FakeShardProcess([None], communicate_times_out=True)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=1.0,
        spawner=_StubSpawner([process]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert process.terminated
    assert "pytest shard did not exit after termination" not in capsys.readouterr().err


def test_completed_shard_cleans_descendants_before_draining_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    process = _FakeShardProcess([0])

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=_StubSpawner([process]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    assert process.reaped
    assert process.orphans_cleaned


def test_completed_shard_cleanup_precedes_terminal_poll(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    process = _FakeShardProcess([0])
    cleanup_observations: list[bool] = []
    original_cleanup = process.cleanup_orphans

    def observe_cleanup() -> None:
        cleanup_observations.append(process.reaped)
        original_cleanup()

    process.cleanup_orphans = observe_cleanup

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=_StubSpawner([process]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    assert cleanup_observations == [True]


def test_pipe_drain_timeout_still_cleans_shard_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    process = _FakeShardProcess([0], communicate_times_out=True)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=_StubSpawner([process]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py",),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    assert not process.reaped
    assert process.orphans_cleaned


def test_run_test_suites_charges_static_discovery_to_parent_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py",),
    )
    clock = _FakeClock()
    spawner = _StubSpawner([])

    def discover(_cwd: Path) -> tuple[str, ...]:
        clock.advance(5.0)
        return ("tests/test_alpha.py",)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=spawner,
        file_discoverer=discover,
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 124
    assert spawner.calls == []


def test_profile_durations_is_accepted_and_dispatches_to_per_file_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durations profile dispatches to the per-file duration runner."""
    calls: list[Path] = []
    monkeypatch.setattr(
        test_suites_module, "_run_durations_profile", lambda cwd: calls.append(cwd) or 7
    )

    exit_code = test_suites_module.main(("--profile", "durations"))

    assert exit_code == 7
    assert calls == [Path.cwd()]


def test_profile_durations_appears_in_help_text() -> None:
    """Unknown-profile SystemExit help text mentions the durations profile."""
    with pytest.raises(SystemExit, match="--profile durations"):
        test_suites_module.main(("--profile", "unknown"))
