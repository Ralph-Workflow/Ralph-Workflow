"""Discovery, partitioning, and weight estimation tests for the test-suite runner.

This module owns the static AST / partitioning checks for
:mod:`ralph.test_suites`. The shared fakes for the shard-runner orchestration
side (which previously lived here) live in
:mod:`tests._test_test_suites_helpers` so the test runner orchestration
tests can be their own file (``tests/test_test_suites_orchestration.py``)
under the maintained ``1000``-line-per-file cap.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

import ralph.test_suites as test_suites_module
import tests.conftest as conftest_module
from tests._test_test_suites_helpers import (
    EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES,
    _BackpressuredShardProcess,
    _FakeClock,
    _FakeShardProcess,
    _StubSpawner,
)

# --- Testing-policy white-box exception (frozen routing contract) ---------
#
# ``ralph/test_suites.py`` is FROZEN for this unit and owns the shard routing
# contract: the E2E shard worker count, the per-file weight estimation fast
# path, the discovery weight/source caches and the required-E2E weight
# multiplier. None of these values is rendered by the documented CLI output of
# the suite runner (they are internal scheduling inputs), and the module cannot
# be refactored to expose them in this unit. The public surface
# (``discover_test_files`` / ``discover_unit_test_files`` /
# ``discover_integration_test_files`` / ``discover_subprocess_e2e_files`` /
# ``estimate_test_file_weight`` / ``partition_selected_files`` /
# ``validate_exact_file_assignment``) is exercised by the behavioural cases
# below; every assertion that still reaches into a frozen private is marked
# ``# policy: white-box-frozen`` and is an explicitly quoted exception.
#
# Quoted policy clause -- docs/ralph-workflow-policy/testing-policy.md,
# "Default requirements":
#   "White-box tests that reach into private internals are permitted only
#    when no observable surface can express the regression."
#
# Why the exception holds here: the frozen routing contract has no public
# observable for the pinned values (see the enumeration above), so the
# regression cannot be expressed through the public surface.
POLICY_WHITE_BOX_EXCEPTION = (
    "White-box tests that reach into private internals are permitted only when "
    "no observable surface can express the regression."
)

#: Mirror of ``SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES`` in
#: ``ralph/test_suites.py`` -- pinned verbatim so this module fails when the
#: audit-exception registry drifts, exactly like the required registry
#: above. These files run real Git crossings in the default ``make test``
#: profile and are audit-sanctioned; they are NOT pytest-selection
#: requirements (``tests/conftest.py`` cannot be discovered at all).
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
        == conftest_module._AGY_DEFAULT_TEST_PATHS  # policy: white-box-frozen
    )


def test_fast_profile_registry_is_a_fixed_nonempty_routing_contract() -> None:
    """S-5: test-fast covers the statically named suite-routing boundary only."""
    assert test_suites_module.FAST_PROFILE_TEST_FILES == EXPECTED_FAST_TEST_FILES
    assert all(not path.startswith("tests/integration/") for path in EXPECTED_FAST_TEST_FILES)
    assert not set(EXPECTED_FAST_TEST_FILES) & set(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)


def test_required_e2e_shard_xdist_workers_constant_is_pinned_to_twelve() -> None:
    """Use twelve workers for the isolated real-git shard.

    wt-03-fix-bug pinned four to hold total concurrent processes at 12
    (8 general + 4 E2E) on the then-maintained 12-core host. On the current
    40-core host the eight general shards finish in 25-35 s while the
    real-git shard alone spends ~44 s of pytest time on 721 collected
    tests, so it alone sets the ``make test`` wall clock. Charged together
    with ``make test-verification-smoke`` (13 s) that pair overflowed the
    immutable cumulative 60 s budget. The value stays a strict, pinned
    runner-architecture constant -- a change here must be deliberate.
    """
    assert test_suites_module._REQUIRED_E2E_SHARD_XDIST_WORKERS == "12"  # policy: white-box-frozen


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


def test_sanctioned_default_lane_real_git_registry_is_pinned() -> None:
    """The audit-exception registry is pinned and never overlaps selection.

    ``SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES`` sanctions real-Git crossings
    for the test-policy audit only; it must not double as a pytest-selection
    requirement (``tests/conftest.py`` is not discoverable, and requiring it
    would fail ``make test`` selection unconditionally).
    """
    assert (
        test_suites_module.SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES
        == EXPECTED_SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES
    )
    assert len(set(test_suites_module.SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES)) == len(
        EXPECTED_SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES
    )
    assert not (
        set(test_suites_module.SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES)
        & set(test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES)
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
        test_suites_module._discover_all_test_files(tmp_path),  # policy: white-box-frozen
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

    assert test_suites_module._fast_test_count(source) == 3  # policy: white-box-frozen
    parametrized = """
import pytest
@pytest.mark.parametrize("value", [1, 2, 3, 4, 5])
def test_values(value: int) -> None:
    assert value
"""
    assert test_suites_module._fast_test_count(parametrized) == 5  # policy: white-box-frozen
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
    assert (
        test_suites_module._fast_test_count(multiline_parametrized) == 4
    )  # policy: white-box-frozen
    assert test_suites_module._fast_test_count("# helper only\n") == 0  # policy: white-box-frozen
    # Defensive minimum: even empty sources get weight 1 for shard placement.
    assert test_suites_module._fast_test_count("") == 0  # policy: white-box-frozen


def test_static_discovery_populates_weight_cache_for_retained_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """discover_test_files must populate the per-file weight cache in one pass.

    Without this in-line pre-population ``_test_file_weight`` re-parses every
    cached source with the Python syntax-tree parser after discovery, costing
    ~5s on a 1.3k file tree. The parent process pays that cost before any
    shard is spawned, so it directly inflates the wall clock against the 60s
    combined budget enforced by
    ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``.
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

    assert (
        test_suites_module._FILE_WEIGHT_CACHE.get("tests/test_retained_one.py") == 1
    )  # policy: white-box-frozen
    assert (
        test_suites_module._FILE_WEIGHT_CACHE.get("tests/test_retained_two.py") == 2
    )  # policy: white-box-frozen


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

    assert test_suites_module._FILE_WEIGHT_CACHE[required] == 1  # policy: white-box-frozen
    assert test_suites_module._test_file_weight(tmp_path, required) == (  # policy: white-box-frozen
        test_suites_module._REQUIRED_E2E_WEIGHT_MULTIPLIER  # policy: white-box-frozen
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
        test_suites_module._test_file_weight(  # policy: white-box-frozen
            Path("/unused"),
            relative_path,
        )
        == test_suites_module._REQUIRED_E2E_WEIGHT_MULTIPLIER  # policy: white-box-frozen
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
    assert test_suites_module._FILE_SOURCE_CACHE.keys() >= set(
        discovered
    )  # policy: white-box-frozen


def _load_module_from_path(module_name: str, path: Path) -> ModuleType:
    """Import *path* as a standalone module.

    The module object is deliberately NOT bound into this test module's
    namespace: nothing it defines may be collected by pytest here.
    """
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"{path} must be importable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_marker_names(module: ModuleType) -> set[str]:
    """Return the marker names pytest collects from *module*.

    ``pytestmark`` is pytest's own public module-level marker attribute; a
    module may set it to a single marker or to a sequence of markers.
    """
    marks = getattr(module, "pytestmark", [])
    candidates = list(marks) if isinstance(marks, (list, tuple)) else [marks]
    found = {getattr(candidate, "name", None) for candidate in candidates}
    return {name for name in found if isinstance(name, str)}


def _shard_marker_expression(command: tuple[str, ...]) -> str:
    """Return the ``-m`` marker expression of a shard command (the last ``-m``)."""
    marker_flag = len(command) - 1 - command[::-1].index("-m")
    return command[marker_flag + 1]


def test_real_pty_completion_no_terminal_lost_has_required_auto_integrate_e2e_and_smoke() -> None:
    """The real-PTY regression file must carry the required auto-integrate E2E
    marker and the smoke marker.

    wt-03-fix-bug: subprocess_e2e removed; the file runs in the E2E shard via
    required_auto_integrate_e2e (which provides the same make-test coverage
    guarantee). The markers are read from the module's PUBLIC ``pytestmark``
    attribute -- the same attribute pytest itself collects -- and not from
    the file's source text, so no other test file's source is parsed here.
    """
    target_rel = "tests/agents/invoke/test_real_pty_completion_no_terminal_lost.py"
    repo_root = Path(__file__).resolve().parent.parent
    target_path = repo_root / target_rel
    assert target_path.is_file(), f"{target_rel} must exist on disk"
    assert target_rel in test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES, (
        f"{target_rel} must be in REQUIRED_AUTO_INTEGRATE_E2E_FILES"
    )
    target_module = _load_module_from_path("pty_completion_target", target_path)
    markers = _module_marker_names(target_module)
    assert {"required_auto_integrate_e2e", "smoke"} <= markers, (
        f"module-level markers must define both required_auto_integrate_e2e and smoke, found: {markers}"
    )
    assert hasattr(target_module, "test_request_interactive_exit_does_not_lose_returncode"), (
        f"test_request_interactive_exit_does_not_lose_returncode must be defined in {target_rel}"
    )

    # The Makefile is the documented user-facing command surface: the
    # ``test-verification-smoke`` target is how an operator runs this file.
    # Reading a Makefile target body is a public-surface read, not
    # white-box coupling into another test's implementation.
    makefile_lines = (repo_root / "Makefile").read_text(encoding="utf-8").splitlines()
    smoke_target_body: list[str] = []
    for line in makefile_lines[makefile_lines.index("test-verification-smoke:") + 1 :]:
        if not line.startswith("\t"):
            break
        smoke_target_body.append(line)
    assert smoke_target_body, "test-verification-smoke target not found in Makefile"
    assert any(target_rel in line for line in smoke_target_body), (
        f"smoke target in Makefile must contain {target_rel}"
    )


def test_required_auto_integrate_e2e_smoke_module_routes_to_the_required_e2e_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A module marked ``required_auto_integrate_e2e`` + ``smoke`` runs on the
    required auto-integrate E2E shard, not on the default suite's shards.

    The synthetic ``tests/`` tree holds the three module shapes the routing
    contract distinguishes: the marked one, a plain unmarked one, and a
    module-marked ``subprocess_e2e`` one. The public runner must keep the
    marked module out of the subprocess-E2E lane, route it to the required
    auto-integrate E2E shard, and leave the plain module on the general
    shard.
    """
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    marked_rel = "tests/test_marked_required_e2e.py"
    (tests_root / "test_marked_required_e2e.py").write_text(
        "import pytest\n\n"
        "pytestmark = [pytest.mark.required_auto_integrate_e2e, pytest.mark.smoke]\n\n\n"
        "def test_request_interactive_exit_does_not_lose_returncode() -> None:\n    pass\n",
        encoding="utf-8",
    )
    plain_rel = "tests/test_plain_default.py"
    (tests_root / "test_plain_default.py").write_text(
        "def test_plain() -> None:\n    pass\n", encoding="utf-8"
    )
    subprocess_e2e_rel = "tests/test_subprocess_e2e_only.py"
    (tests_root / "test_subprocess_e2e_only.py").write_text(
        "import pytest\n\npytestmark = pytest.mark.subprocess_e2e\n", encoding="utf-8"
    )
    monkeypatch.setattr(test_suites_module, "REQUIRED_AUTO_INTEGRATE_E2E_FILES", (marked_rel,))
    test_suites_module.reset_discovery_cache()

    # Static discovery keeps the marked module (a ``smoke`` marker does not
    # hide a file from static discovery) and drops the subprocess-E2E one.
    assert set(test_suites_module.discover_test_files(tmp_path)) == {marked_rel, plain_rel}
    # The marked module is NOT in the subprocess-E2E lane: it carries no
    # ``subprocess_e2e`` marker, so the registry reaches it instead.
    assert test_suites_module.discover_subprocess_e2e_files(tmp_path) == (subprocess_e2e_rel,)

    clock = _FakeClock()
    spawner = _StubSpawner([_FakeShardProcess([0]), _FakeShardProcess([0])])
    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path, spawner=spawner, monotonic=clock, wait=clock.advance
    )
    assert exit_code == 0
    # The marked module is routed to the required auto-integrate E2E shard;
    # the plain module stays on the default suite's general shard.
    assert spawner.manifest_files == [(plain_rel,), (marked_rel,)]
    # The default profile's expression selects ``required_auto_integrate_e2e``
    # modules, so the smoke-marked module still runs on the default profile.
    assert {_shard_marker_expression(c) for c, _cwd, _env in spawner.calls} == {
        "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e"
    }

    # The focused profiles apply the plain ``not subprocess_e2e and not
    # smoke`` expression, under which the smoke-marked module is NOT
    # collected while the plain, unmarked module is.
    focused_clock = _FakeClock()
    focused_spawner = _StubSpawner([_FakeShardProcess([0]) for _ in range(4)])
    focused_exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path, spawner=focused_spawner, profile="unit", monotonic=focused_clock
    )
    assert focused_exit_code == 0
    assert {_shard_marker_expression(c) for c, _cwd, _env in focused_spawner.calls} == {
        "not subprocess_e2e and not smoke"
    }
