from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

import ralph.test_suites as test_suites_module
import tests.conftest as conftest_module
from tests._test_test_suites_helpers import (
    EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES,
    EXPECTED_SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES,
    _BackpressuredShardProcess,
    _FakeClock,
    _FakeShardProcess,
    _StubSpawner,
)

POLICY_WHITE_BOX_EXCEPTION = (
    "White-box tests that reach into private internals are permitted only when "
    "no observable surface can express the regression."
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
    assert test_suites_module.FAST_PROFILE_TEST_FILES == EXPECTED_FAST_TEST_FILES
    assert all(not path.startswith("tests/integration/") for path in EXPECTED_FAST_TEST_FILES)
    assert not set(EXPECTED_FAST_TEST_FILES) & set(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)


def test_required_e2e_lane_shard_count_is_pinned_to_six() -> None:
    assert test_suites_module._REQUIRED_E2E_SHARD_COUNT == 6


def test_required_auto_integrate_e2e_registry_matches_discovery_contract() -> None:
    assert (
        test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES
        == EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )
    assert len(set(test_suites_module.REQUIRED_AUTO_INTEGRATE_E2E_FILES)) == len(
        EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES
    )


def test_sanctioned_default_lane_real_git_registry_is_pinned() -> None:
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
    assert not (
        set(EXPECTED_SANCTIONED_DEFAULT_LANE_REAL_GIT_FILES)
        & set(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)
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
    assert test_suites_module._fast_test_count("") == 0


def test_static_discovery_populates_weight_cache_for_retained_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

    assert test_suites_module.estimate_test_file_weight(source) == 8


def test_estimate_test_file_weight_handles_syntax_error_gracefully() -> None:
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


def test_static_discovery_drops_modules_whose_every_test_is_deselected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_all_deselected.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.subprocess_e2e\n"
        "def test_first() -> None: pass\n\n"
        "@pytest.mark.smoke\n"
        "def test_second() -> None: pass\n",
        encoding="utf-8",
    )
    (tests_root / "test_partially_deselected.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.subprocess_e2e\n"
        "def test_deselected() -> None: pass\n\n"
        "def test_selected() -> None: pass\n",
        encoding="utf-8",
    )
    (tests_root / "test_required_marker.py").write_text(
        "import pytest\n\n"
        "@pytest.mark.subprocess_e2e\n"
        "@pytest.mark.required_auto_integrate_e2e\n"
        "def test_reselected() -> None: pass\n",
        encoding="utf-8",
    )
    (tests_root / "test_unparsable.py").write_text("def test_broken(:\n", encoding="utf-8")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    test_suites_module.reset_discovery_cache()

    assert set(test_suites_module.discover_test_files(tmp_path)) == {
        "tests/test_partially_deselected.py",
        "tests/test_required_marker.py",
        "tests/test_unparsable.py",
    }


def test_static_discovery_keeps_modules_whose_class_tests_are_deselected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_deselected_class.py").write_text(
        "import pytest\n\n"
        "class TestGroup:\n"
        "    @pytest.mark.subprocess_e2e\n"
        "    def test_deselected(self) -> None: pass\n",
        encoding="utf-8",
    )
    (tests_root / "test_selected_class.py").write_text(
        "import pytest\n\n"
        "class TestGroup:\n"
        "    @pytest.mark.subprocess_e2e\n"
        "    def test_deselected(self) -> None: pass\n\n"
        "    def test_selected(self) -> None: pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        (),
    )
    test_suites_module.reset_discovery_cache()

    assert set(test_suites_module.discover_test_files(tmp_path)) == {"tests/test_selected_class.py"}


def test_static_discovery_finds_pytest_patterns_and_required_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_static_discovery_populates_source_cache_for_retained_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def _load_module_from_path(module_name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None, f"{path} must be importable"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _module_marker_names(module: ModuleType) -> set[str]:
    marks = getattr(module, "pytestmark", [])
    candidates = list(marks) if isinstance(marks, (list, tuple)) else [marks]
    found = {getattr(candidate, "name", None) for candidate in candidates}
    return {name for name in found if isinstance(name, str)}


def _shard_marker_expression(command: tuple[str, ...]) -> str:
    marker_flag = len(command) - 1 - command[::-1].index("-m")
    return command[marker_flag + 1]


def test_real_pty_completion_no_terminal_lost_has_required_auto_integrate_e2e_and_smoke() -> None:
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

    assert set(test_suites_module.discover_test_files(tmp_path)) == {marked_rel, plain_rel}
    assert test_suites_module.discover_subprocess_e2e_files(tmp_path) == (subprocess_e2e_rel,)

    clock = _FakeClock()
    spawner = _StubSpawner([_FakeShardProcess([0]), _FakeShardProcess([0])])
    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path, spawner=spawner, monotonic=clock, wait=clock.advance
    )
    assert exit_code == 0
    assert spawner.manifest_files == [(plain_rel,), (marked_rel,)]
    assert {_shard_marker_expression(c) for c, _cwd, _env in spawner.calls} == {
        "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e"
    }

    focused_clock = _FakeClock()
    focused_spawner = _StubSpawner([_FakeShardProcess([0]) for _ in range(4)])
    focused_exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path, spawner=focused_spawner, profile="unit", monotonic=focused_clock
    )
    assert focused_exit_code == 0
    assert {_shard_marker_expression(c) for c, _cwd, _env in focused_spawner.calls} == {
        "not subprocess_e2e and not smoke"
    }
