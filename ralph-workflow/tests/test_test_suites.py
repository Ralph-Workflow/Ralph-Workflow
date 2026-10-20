"""Discovery, partitioning, and weight estimation tests for the test-suite runner.

This module owns the static AST / partitioning checks for
:mod:`ralph.test_suites`. The shared fakes for the shard-runner orchestration
side (which previously lived here) live in
:mod:`tests._test_test_suites_helpers` so the test runner orchestration
tests can be their own file (``tests/test_test_suites_orchestration.py``)
under the maintained ``1000``-line-per-file cap.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import ralph.test_suites as test_suites_module
import tests.conftest as conftest_module
from tests._test_test_suites_helpers import (
    _BackpressuredShardProcess,
    _FakeClock,
    _FakeShardProcess,
    _StubSpawner,
)

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
    "tests/mcp/test_e17_state_continuity.py",
    "tests/mcp/test_e18_old_session_id_accepted.py",
    "tests/mcp/test_e24_fresh_stage_id.py",
    "tests/mcp/test_e25_candidate_survives_agent_tmp_deletion.py",
    "tests/mcp/test_e26_broken_workspace_package.py",
    "tests/mcp/test_e33_listening_fd_real_child.py",
    "tests/mcp/test_e3x_transport_gap_typed_retry.py",
    "tests/mcp/test_e42_two_supervisors_one_replacement.py",
    "tests/mcp/test_e46_parent_sigkill_no_orphans.py",
    "tests/mcp/test_e72_footprint_real_child.py",
    "tests/mcp/test_handover_inflight_real_child.py",
    "tests/mcp/test_replacement_shutdown_signals_real_child.py",
    "tests/mcp/test_root_cause_real_child_bridge.py",
    "tests/mcp/test_s1_independent_counters_contract.py",
    "tests/mcp/test_single_acceptor_instrumented.py",
    # wt-19-fix-long-exec-tasks — exec job flow smokes (real subprocess)
    # Real-process proofs for the liveness assessor + job registry. Both
    # files carry subprocess_e2e + required_auto_integrate_e2e markers.
    "tests/test_exec_smoke_subprocess_e2e.py",
    "tests/test_exec_job_shutdown_subprocess_e2e.py",
)
EXPECTED_FAST_TEST_FILES = (
    "tests/test_makefile_verification_workflow.py",
    "tests/test_test_suites.py",
    "tests/test_test_suites_orchestration.py",
)


@pytest.mark.parametrize(
    ("baseline_exit", "elapsed", "expected"), ((0, 0.0, 0), (1, 0.0, 1), (0, 60.0, 0))
)
def test_product_baseline_shares_the_parallel_fanout_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    baseline_exit: int,
    elapsed: float,
    expected: int,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "0")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    clock = _FakeClock()
    baseline = _FakeShardProcess([baseline_exit], on_communicate=lambda: clock.advance(elapsed))
    spawner = _StubSpawner([baseline, _FakeShardProcess([0]), _FakeShardProcess([0])])
    baseline_path = "tests/workspace/test_workspace_product_baselines.py"
    selected = (baseline_path, "tests/test_alpha.py", "tests/test_bravo.py")

    result = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        monotonic=clock,
        file_discoverer=lambda _cwd: selected,
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert result == expected
    assigned = {path for manifest in spawner.manifest_files for path in manifest}
    assert assigned == set(selected)
    assert len(spawner.manifest_files) == 2
    assert "-n" not in spawner.calls[0][0]


def test_run_test_suites_drains_backpressured_shard_pipe_instead_of_timing_out(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())
    blocked = _BackpressuredShardProcess(stdout=b"pipe drained output\n")
    done = _FakeShardProcess([0])
    clock = _FakeClock()

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        suite_timeout_seconds=5.0,
        spawner=_StubSpawner([blocked, done]),
        file_discoverer=lambda _cwd: ("tests/test_alpha.py", "tests/test_bravo.py"),
        file_weigher=lambda _cwd, _path: 1,
        monotonic=clock,
        wait=clock.advance,
    )

    assert exit_code == 0
    assert blocked.reaped
    assert not blocked.terminated
    assert "pipe drained output" in capsys.readouterr().out


def test_default_routing_allowlist_is_exact_and_immutable() -> None:
    """AGY's two deterministic regressions stay in the default suite."""
    assert (
        frozenset(
            {
                "tests/test_agy_pty_invocation.py",
                "tests/test_agy_runtime_home_auth.py",
            }
        )
        == conftest_module._AGY_DEFAULT_TEST_PATHS
    )


def test_fast_profile_registry_is_a_fixed_nonempty_routing_contract() -> None:
    """S-5: test-fast covers the statically named suite-routing boundary only."""
    assert test_suites_module.FAST_PROFILE_TEST_FILES == EXPECTED_FAST_TEST_FILES
    assert all(not path.startswith("tests/integration/") for path in EXPECTED_FAST_TEST_FILES)
    assert not set(EXPECTED_FAST_TEST_FILES) & set(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)


def test_required_e2e_shard_xdist_workers_constant_is_pinned_to_eight() -> None:
    """Use eight workers for the isolated real-git shard.

    wt-03-fix-bug pinned four to hold total concurrent processes at 12
    (8 general + 4 E2E) on the then-maintained 12-core host. On the current
    40-core host the eight general shards finish in 25-35 s while the
    real-git shard alone spends ~44 s of pytest time on 721 collected
    tests, so it alone sets the ``make test`` wall clock. Charged together
    with ``make test-verification-smoke`` (13 s) that pair overflowed the
    immutable cumulative 60 s budget. The value stays a strict, pinned
    runner-architecture constant -- a change here must be deliberate.
    """
    assert test_suites_module._REQUIRED_E2E_SHARD_XDIST_WORKERS == "8"


def test_required_auto_integrate_e2e_registry_matches_discovery_contract() -> None:
    """The discovery suite enforces the required auto-integrate registry.

    Mirrors the orchestration-side contract test so both test modules
    independently fail when the production registry drifts.
    """
    assert (
        test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES
        == EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )
    assert len(set(test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES)) == len(
        EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )


def test_partition_selected_files_assigns_every_file_once_deterministically() -> None:
    selected = (
        "tests/test_delta.py",
        "tests/test_alpha.py",
        "tests/test_charlie.py",
        "tests/test_bravo.py",
    )

    shards = test_suites_module.partition_selected_files(selected, worker_count=3)

    assert shards == (
        ("tests/test_alpha.py", "tests/test_delta.py"),
        ("tests/test_bravo.py",),
        ("tests/test_charlie.py",),
    )
    test_suites_module.validate_exact_file_assignment(selected, shards)


def test_partition_selected_files_balances_static_weights_deterministically() -> None:
    selected = (
        "tests/test_alpha.py",
        "tests/test_bravo.py",
        "tests/test_charlie.py",
        "tests/test_delta.py",
    )
    weights = {
        "tests/test_alpha.py": 8,
        "tests/test_bravo.py": 7,
        "tests/test_charlie.py": 6,
        "tests/test_delta.py": 5,
    }

    shards = test_suites_module.partition_selected_files(
        selected,
        worker_count=2,
        file_weights=weights,
    )

    assert shards == (
        ("tests/test_alpha.py", "tests/test_delta.py"),
        ("tests/test_bravo.py", "tests/test_charlie.py"),
    )
    test_suites_module.validate_exact_file_assignment(selected, shards)


def test_partition_selected_files_minimizes_heavy_e2e_shard_load() -> None:
    """The heaviest E2E file is isolated by deterministic LPT placement."""
    selected = (
        "tests/test_auto_integrate_recovery.py",
        "tests/test_auto_integrate_refresh_contract.py",
        "tests/test_auto_integrate_rebase_conflict_e2e.py",
        "tests/test_auto_integrate_fleet_conflict_e2e.py",
        "tests/test_auto_integrate_worktree_sync.py",
        "tests/test_alpha.py",
        "tests/test_bravo.py",
        "tests/test_charlie.py",
    )
    weights = {
        "tests/test_auto_integrate_recovery.py": 1500,
        "tests/test_auto_integrate_refresh_contract.py": 420,
        "tests/test_auto_integrate_rebase_conflict_e2e.py": 60,
        "tests/test_auto_integrate_fleet_conflict_e2e.py": 60,
        "tests/test_auto_integrate_worktree_sync.py": 60,
        "tests/test_alpha.py": 8,
        "tests/test_bravo.py": 7,
        "tests/test_charlie.py": 6,
    }

    shards = test_suites_module.partition_selected_files(
        selected,
        worker_count=3,
        file_weights=weights,
    )

    assert shards == (
        ("tests/test_auto_integrate_recovery.py",),
        ("tests/test_auto_integrate_refresh_contract.py",),
        (
            "tests/test_alpha.py",
            "tests/test_auto_integrate_fleet_conflict_e2e.py",
            "tests/test_auto_integrate_rebase_conflict_e2e.py",
            "tests/test_auto_integrate_worktree_sync.py",
            "tests/test_bravo.py",
            "tests/test_charlie.py",
        ),
    )
    test_suites_module.validate_exact_file_assignment(selected, shards)


def test_static_profile_discovery_regression_partitions_unit_and_integration_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S-2: focused profiles statically partition test files before pytest starts."""
    tests_root = tmp_path / "tests"
    integration_root = tests_root / "integration"
    integration_root.mkdir(parents=True)
    (tests_root / "test_unit.py").write_text("def test_unit() -> None: pass\n", encoding="utf-8")
    (integration_root / "test_integration.py").write_text(
        "def test_integration() -> None: pass\n", encoding="utf-8"
    )
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", ())

    unit_files = test_suites_module.discover_unit_test_files(tmp_path)
    integration_files = test_suites_module.discover_integration_test_files(tmp_path)

    assert unit_files == ("tests/test_unit.py",)
    assert integration_files == ("tests/integration/test_integration.py",)
    test_suites_module.validate_exact_file_assignment(
        test_suites_module._discover_all_test_files(tmp_path),
        (unit_files, integration_files),
    )


def test_static_subprocess_e2e_discovery_selects_only_marked_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_e2e.py").write_text(
        "import pytest\npytestmark = pytest.mark.subprocess_e2e\n",
        encoding="utf-8",
    )
    (tests_root / "test_unit.py").write_text("def test_unit(): pass\n", encoding="utf-8")
    required = tests_root / "test_cli_commit_command.py"
    required.write_text("def test_required(): pass\n", encoding="utf-8")

    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_cli_commit_command.py",),
    )
    assert test_suites_module.discover_subprocess_e2e_files(tmp_path) == ("tests/test_e2e.py",)


def test_estimate_test_file_weight_counts_sync_async_and_method_tests() -> None:
    source = """
def test_top_level() -> None:
    pass

async def test_async_case() -> None:
    pass

class TestCases:
    def test_method(self) -> None:
        pass

# def test_commented_out() -> None:
"""

    assert test_suites_module.estimate_test_file_weight(source) == 3
    assert test_suites_module.estimate_test_file_weight("# helper only\n") == 1


def test_estimate_test_file_weight_accounts_for_literal_parametrized_collection() -> None:
    source = """
import pytest

@pytest.mark.parametrize(("value", "expected"), [(1, "one"), (2, "two"), (3, "three")])
def test_value(value: int, expected: str) -> None:
    assert value

@pytest.mark.parametrize("item", range(5))
def test_dynamic_item(item: int) -> None:
    assert item >= 0

@pytest.mark.parametrize(argnames="keyword_item", argvalues=["one", "two"])
def test_keyword_item(keyword_item: str) -> None:
    assert keyword_item
"""

    assert test_suites_module.estimate_test_file_weight(source) == 6


def test_fast_test_count_counts_test_definitions_without_parsing() -> None:
    """The regex counter backs the parent-process weight pre-population."""
    source = """
def test_top_level() -> None:
    pass

async def test_async_case() -> None:
    pass

class TestCases:
    def test_method(self) -> None:
        pass

# def test_commented_out() -> None:
"""

    assert test_suites_module._fast_test_count(source) == 3
    parametrized = """
import pytest
@pytest.mark.parametrize("value", [1, 2, 3, 4, 5])
def test_values(value: int) -> None:
    assert value
"""
    assert test_suites_module._fast_test_count(parametrized) == 5
    multiline_parametrized = """
import pytest
@pytest.mark.parametrize("value", [
    1,
    2,
    3,
    4,
])
def test_multiline_values(value: int) -> None:
    assert value
"""
    assert test_suites_module._fast_test_count(multiline_parametrized) == 4
    assert test_suites_module._fast_test_count("# helper only\n") == 0
    # Defensive minimum: even empty sources get weight 1 for shard placement.
    assert test_suites_module._fast_test_count("") == 0


def test_static_discovery_populates_weight_cache_for_retained_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """discover_test_files must populate the per-file weight cache in one pass.

    Without this in-line pre-population ``_test_file_weight`` re-parses every
    cached source through ``ast.parse`` after discovery, costing ~5s on a 1.3k
    file tree. The parent process pays that cost before any shard is spawned,
    so it directly inflates the wall clock against the 60s combined budget
    enforced by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``.
    """
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    retained_one = tests_root / "test_retained_one.py"
    retained_one.write_text(
        "import pytest\n\n@pytest.mark.timeout_seconds(1)\ndef test_a() -> None: pass\n",
        encoding="utf-8",
    )
    retained_two = tests_root / "test_retained_two.py"
    retained_two.write_text(
        "def test_b() -> None: pass\n\ndef test_c() -> None: pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    test_suites_module.reset_discovery_cache()

    test_suites_module.discover_test_files(tmp_path)

    assert test_suites_module._FILE_WEIGHT_CACHE.get("tests/test_retained_one.py") == 1
    assert test_suites_module._FILE_WEIGHT_CACHE.get("tests/test_retained_two.py") == 2


def test_static_discovery_keeps_required_real_git_weight_unmultiplied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    required = "tests/test_real_git.py"
    (tests_root / "test_real_git.py").write_text(
        "def test_real_git() -> None: pass\n", encoding="utf-8"
    )
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", (required,))
    test_suites_module.reset_discovery_cache()

    test_suites_module.discover_test_files(tmp_path)

    assert test_suites_module._FILE_WEIGHT_CACHE[required] == 1
    assert test_suites_module._test_file_weight(tmp_path, required) == (
        test_suites_module._REQUIRED_E2E_WEIGHT_MULTIPLIER
    )


def test_required_real_git_file_weight_accounts_for_process_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relative_path = "tests/test_real_git.py"
    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _path, *, encoding: "def test_real_git() -> None:\n    pass\n",
    )
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (relative_path,),
    )

    assert (
        test_suites_module._test_file_weight(
            Path("/unused"),
            relative_path,
        )
        == test_suites_module._REQUIRED_E2E_WEIGHT_MULTIPLIER
    )


def test_estimate_test_file_weight_matches_class_scope_walk_for_known_samples() -> None:
    """The fast class-scope walker must match a literal parametrization sample.

    The class-scope walker is the optimized replacement for the
    ``ast.walk``-based implementation: it descends through ``ClassDef``
    bodies but does not visit nested-function bodies (pytest does not
    collect them anyway). This test pins the contract that a
    well-formed test file with both module-level and class-level
    parametrized tests still produces the same weight as the old
    ``ast.walk`` walker.
    """
    source = '''
import pytest


@pytest.mark.parametrize(("value", "expected"), [(1, "one"), (2, "two"), (3, "three")])
def test_module_level_parametrized(value: int, expected: str) -> None:
    assert value


def test_module_level_plain() -> None:
    assert True


class TestClassScope:
    @pytest.mark.parametrize("arg", ["a", "b", "c"])
    def test_method_parametrized(self, arg: str) -> None:
        assert arg

    def test_method_plain(self) -> None:
        assert True


def helper_helper_function() -> None:
    """This is not a test; the walker must not count it."""

    def test_nested_under_helper() -> None:
        """Nested function definitions are not collected by pytest."""
        assert True
'''

    # 1 module-level parametrized (3 cases) + 1 module-level plain + 1 class
    # method parametrized (3 cases) + 1 class method plain = 8.
    assert test_suites_module.estimate_test_file_weight(source) == 8


def test_estimate_test_file_weight_handles_syntax_error_gracefully() -> None:
    """A file that fails to parse must report weight 1 rather than raising.

    The walker only walks visible nodes; if the AST step fails, the
    fallback weight of 1 keeps the shard balancer from crashing the
    whole ``make test`` run on a stray bad module.
    """
    assert test_suites_module.estimate_test_file_weight("def @broken:") == 1


def test_validate_exact_file_assignment_rejects_duplicate_file() -> None:
    selected = ("tests/test_alpha.py", "tests/test_bravo.py")
    shards = (("tests/test_alpha.py",), ("tests/test_alpha.py", "tests/test_bravo.py"))

    with pytest.raises(RuntimeError, match=r"duplicate.*tests/test_alpha.py"):
        test_suites_module.validate_exact_file_assignment(selected, shards)


def test_validate_exact_file_assignment_rejects_missing_and_unexpected_files() -> None:
    selected = ("tests/test_alpha.py", "tests/test_bravo.py")
    shards = (("tests/test_alpha.py", "tests/test_charlie.py"),)

    with pytest.raises(
        RuntimeError,
        match=r"missing.*tests/test_bravo.py.*unexpected.*tests/test_charlie.py",
    ):
        test_suites_module.validate_exact_file_assignment(selected, shards)


def test_partition_selected_files_rejects_non_positive_worker_count() -> None:
    with pytest.raises(ValueError, match="worker_count must be positive"):
        test_suites_module.partition_selected_files(("tests/test_alpha.py",), worker_count=0)


def test_static_discovery_excludes_only_module_marked_e2e_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_e2e_only.py").write_text(
        "import pytest\npytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]\n",
        encoding="utf-8",
    )
    (tests_root / "test_mixed.py").write_text(
        "import pytest\n\n@pytest.mark.subprocess_e2e\ndef test_boundary() -> None: pass\n\ndef test_default() -> None: pass\n",
        encoding="utf-8",
    )
    (tests_root / "test_reassigned.py").write_text(
        "import pytest\npytestmark = pytest.mark.subprocess_e2e\npytestmark = []\n",
        encoding="utf-8",
    )
    required = tests_root / "test_required.py"
    required.write_text(
        "import pytest\npytestmark = pytest.mark.subprocess_e2e\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_required.py",),
    )

    assert test_suites_module.discover_test_files(tmp_path) == (
        "tests/test_mixed.py",
        "tests/test_reassigned.py",
        "tests/test_required.py",
    )
    assert test_suites_module.discover_subprocess_e2e_files(tmp_path) == (
        "tests/test_e2e_only.py",
        "tests/test_mixed.py",
        "tests/test_reassigned.py",
        "tests/test_required.py",
    )


def test_static_discovery_finds_pytest_patterns_and_required_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery walks the ``tests/`` tree and applies the pytest naming
    pattern. The on-disk shape is exercised against a tiny synthetic
    ``tests/`` tree so the assertion is deterministic and well inside the
    1s per-test ITIMER_REAL budget even under shard-saturated disk
    contention. The full-tree variant walked ``Path.cwd()/tests/`` (~1.3k
    files), and even with a 5s override the cold-cache walk on the
    maintained 8-shard run could exceed the SIGALRM cap, masking the
    assertion the test was trying to pin. The synthetic tree asserts the same observable contract
    (name pattern, sort order, required-file inclusion) without paying the
    per-file read cost.
    """
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_alpha.py").write_text("def test_one() -> None: pass\n", encoding="utf-8")
    (tests_root / "test_beta.py").write_text("def test_two() -> None: pass\n", encoding="utf-8")
    (tests_root / "helper_test.py").write_text("def test_three() -> None: pass\n", encoding="utf-8")
    (tests_root / "support_module.py").write_text("# not a test module\n", encoding="utf-8")
    required = tests_root / "test_required.py"
    required.write_text("def test_required() -> None: pass\n", encoding="utf-8")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_required.py",),
    )
    test_suites_module.reset_discovery_cache()

    discovered = test_suites_module.discover_test_files(tmp_path)

    assert discovered == tuple(sorted(discovered))
    assert set(discovered) == {
        "tests/helper_test.py",
        "tests/test_alpha.py",
        "tests/test_beta.py",
        "tests/test_required.py",
    }
    assert {"tests/test_required.py"} <= set(discovered)


# S-1 (PTY subprocess regression) and S-5 (terminate / reap pool)
# tests moved to tests/test_test_suites_subprocess_orchestration.py
# so this suite stays under the 1000-line audit cap.
def test_static_discovery_populates_source_cache_for_retained_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each retained file's decoded source must end up in the source cache so
    ``_test_file_weight`` does not re-read the same file from disk during
    shard weight computation. The synthetic ``tests/`` tree is small enough
    to fit comfortably inside the 1s ITIMER_REAL per-test budget, so the
    assertion is deterministic under shard-saturated disk contention (the
    previous full-tree variant flaked at >5s on the maintained 32-core
    profile).
    """
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    retained_one = tests_root / "test_retained_one.py"
    retained_one.write_text(
        "import pytest\n\n@pytest.mark.timeout_seconds(1)\ndef test_a() -> None: pass\n",
        encoding="utf-8",
    )
    retained_two = tests_root / "test_retained_two.py"
    retained_two.write_text("def test_b() -> None: pass\n", encoding="utf-8")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    test_suites_module.reset_discovery_cache()

    discovered = test_suites_module.discover_test_files(tmp_path)

    assert set(discovered) == {
        "tests/test_retained_one.py",
        "tests/test_retained_two.py",
    }
    assert test_suites_module._FILE_SOURCE_CACHE.keys() >= set(discovered)


def test_real_pty_completion_no_terminal_lost_has_required_auto_integrate_e2e_and_smoke() -> None:
    (
        """Verify that test_real_pty_completion_no_terminal_lost.py has required_auto_integrate_e2e/smoke markers.
    wt-03-fix-bug: subprocess_e2e removed; the file runs in the E2E shard via
    required_auto_integrate_e2e (which provides the same make-test coverage guarantee).
    """
        ""
    )
    target_rel = "tests/agents/invoke/test_real_pty_completion_no_terminal_lost.py"
    repo_root = Path(__file__).resolve().parent.parent
    target_path = repo_root / target_rel
    assert target_path.is_file(), f"{target_rel} must exist on disk"
    assert target_rel in test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES, (
        f"{target_rel} must be in REQUIRED_AUTO_INTEGRATE_E2E_FILES"
    )

    tree = ast.parse(target_path.read_text(encoding="utf-8"), filename=str(target_path))

    pytestmark_markers: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "pytestmark":
                    elements = (
                        node.value.elts
                        if isinstance(node.value, (ast.List, ast.Tuple))
                        else [node.value]
                    )
                    for elt in elements:
                        if isinstance(elt, ast.Attribute):
                            pytestmark_markers.add(elt.attr)
                        elif isinstance(elt, ast.Call) and isinstance(elt.func, ast.Attribute):
                            pytestmark_markers.add(elt.func.attr)

    assert {"required_auto_integrate_e2e", "smoke"}.issubset(pytestmark_markers), (
        f"module-level pytestmark must define both required_auto_integrate_e2e and smoke, found: {pytestmark_markers}"
    )

    defined_functions = {
        node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "test_request_interactive_exit_does_not_lose_returncode" in defined_functions, (
        f"test_request_interactive_exit_does_not_lose_returncode must be defined in {target_rel}"
    )

    makefile_path = repo_root / "Makefile"
    assert makefile_path.is_file(), "Makefile must exist on disk"
    makefile_lines = makefile_path.read_text(encoding="utf-8").splitlines()
    smoke_target_body: list[str] = []
    in_target = False
    for line in makefile_lines:
        if not in_target:
            if line.startswith("test-verification-smoke:"):
                in_target = True
            continue
        if not line.startswith("\t"):
            break
        smoke_target_body.append(line.strip())

    assert smoke_target_body, "test-verification-smoke target not found in Makefile"
    assert any(target_rel in line for line in smoke_target_body), (
        f"smoke target in Makefile must contain {target_rel}"
    )
