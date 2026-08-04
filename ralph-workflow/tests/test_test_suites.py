"""Discovery, partitioning, and weight estimation tests for the test-suite runner.

This module owns the static AST / partitioning checks for
:mod:`ralph.test_suites`. The shared fakes for the shard-runner orchestration
side (which previously lived here) live in
:mod:`tests._test_test_suites_helpers` so the test runner orchestration
tests can be their own file (``tests/test_test_suites_orchestration.py``)
under the maintained ``1000``-line-per-file cap.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import ralph.test_suites as test_suites_module

EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES = ("tests/test_auto_integrate_end_to_end.py",)


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
    parametrized = '''
import pytest
@pytest.mark.parametrize("value", [1, 2, 3, 4, 5])
def test_values(value: int) -> None:
    assert value
'''
    assert test_suites_module._fast_test_count(parametrized) == 5
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
    files), and even with a 5s override the cold-cache walk on a 32-shard
    run could exceed the SIGALRM cap, masking the assertion the test was
    trying to pin. The synthetic tree asserts the same observable contract
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

