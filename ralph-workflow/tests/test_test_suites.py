"""Tests for the maintained test-suite runner."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph import test_suites as test_suites_module

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES = ("tests/test_auto_integrate_end_to_end.py",)


class _FakeShardProcess:
    def __init__(
        self,
        returncodes: list[int | None],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        communicate_times_out: bool = False,
    ) -> None:
        self._returncodes = list(returncodes)
        self._last_returncode: int | None = None
        self._stdout = stdout
        self._stderr = stderr
        self._communicate_times_out = communicate_times_out
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
        if self._communicate_times_out:
            raise subprocess.TimeoutExpired(("pytest",), 1.0)
        self.reaped = True
        return self._stdout, self._stderr

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s
        self.terminated = True
        self._last_returncode = -15

    def cleanup_orphans(self) -> None:
        self.orphans_cleaned = True


class _StubSpawner:
    def __init__(self, processes: list[_FakeShardProcess]) -> None:
        self._processes = list(processes)
        self.calls: list[tuple[tuple[str, ...], Path, dict[str, str]]] = []

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> _FakeShardProcess:
        self.calls.append((tuple(command), cwd, dict(env)))
        return self._processes.pop(0)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


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


def test_pytest_shard_processes_disable_background_reaping_and_event_logging() -> None:
    """Shard teardown owns lifecycle cleanup without per-shard background work."""
    policy = test_suites_module._PYTEST_SHARD_PROCESS_MANAGER.policy

    assert policy.log_events is False
    assert policy.enable_zombie_reaper is False


@pytest.mark.parametrize(
    ("cpu_count", "expected_workers"),
    ((None, "1"), (1, "1"), (2, "1"), (16, "15"), (32, "31"), (64, "32")),
)
def test_auto_worker_count_preserves_one_core_and_caps_at_thirty_two(
    monkeypatch: pytest.MonkeyPatch,
    cpu_count: int | None,
    expected_workers: str,
) -> None:
    monkeypatch.delenv("PYTEST_WORKERS", raising=False)
    monkeypatch.setattr(test_suites_module.os, "cpu_count", lambda: cpu_count)

    assert test_suites_module._pytest_workers() == expected_workers


def test_explicit_worker_count_overrides_the_auto_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "3")

    assert test_suites_module._pytest_workers() == "3"


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
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setenv("PYTEST_XDIST_WORKERS_PER_SHARD", "0")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py", "tests/test_bravo.py"),
    )
    processes = [
        _FakeShardProcess([0], stdout=b"alpha passed\n"),
        _FakeShardProcess([0], stdout=b"bravo passed\n"),
    ]
    spawner = _StubSpawner(processes)

    exit_code = test_suites_module.run_test_suites(
        cwd=tmp_path,
        spawner=spawner,
        file_discoverer=lambda _cwd: (
            "tests/test_bravo.py",
            "tests/test_alpha.py",
        ),
        file_weigher=lambda _cwd, _path: 1,
        wait=lambda _seconds: None,
    )

    assert exit_code == 0
    assert [command[3 : command.index("-q")] for command, _cwd, _env in spawner.calls] == [
        ("tests/test_alpha.py",),
        ("tests/test_bravo.py",),
    ]
    assert all("-n" not in call[0] and "--dist" not in call[0] for call in spawner.calls)
    assert all(process.reaped for process in processes)
    env = spawner.calls[0][2]
    assert env[test_suites_module.TEST_TIMEOUT_ENV] == str(
        test_suites_module.DEFAULT_TEST_TIMEOUT_SECONDS
    )
    assert env["RALPH_PYTEST_SUITE_TIMEOUT_SECONDS"] == str(
        test_suites_module.DEFAULT_SUITE_TIMEOUT_SECONDS
    )
    assert "RALPH_VERIFY_REQUIRED_AUTO_INTEGRATE_E2E" not in env


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
        ("tests/test_alpha.py", "tests/test_bravo.py"),
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

    assigned_files = tuple(
        path for command, _cwd, _env in spawner.calls for path in command[3 : command.index("-q")]
    )
    assert exit_code == 0
    assert sorted(assigned_files) == sorted(EXPECTED_REQUIRED_AUTO_INTEGRATE_E2E_FILES)
    assert len(assigned_files) == len(set(assigned_files))


def test_subprocess_e2e_profile_uses_canonical_marker_with_explicit_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "1")
    spawner = _StubSpawner([_FakeShardProcess([0])])
    monkeypatch.setattr(
        test_suites_module,
        "discover_subprocess_e2e_files",
        lambda _cwd: ("tests/test_e2e.py",),
    )

    assert (
        test_suites_module.run_test_suites(
            cwd=tmp_path,
            spawner=spawner,
            file_weigher=lambda _cwd, _path: 1,
            wait=lambda _seconds: None,
            subprocess_e2e_only=True,
        )
        == 0
    )
    command = spawner.calls[0][0]
    assert command[3 : command.index("-q")] == ("tests/test_e2e.py",)
    marker_flag = command.index("-m", command.index("pytest") + 1)
    assert command[marker_flag + 1] == test_suites_module._SUBPROCESS_E2E_MARK_EXPRESSION


def test_run_test_suites_terminates_and_reaps_siblings_on_first_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PYTEST_WORKERS", "2")
    monkeypatch.setattr(
        test_suites_module,
        "REQUIRED_AUTO_INTEGRATE_E2E_FILES",
        ("tests/test_alpha.py", "tests/test_bravo.py"),
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
        ("tests/test_alpha.py", "tests/test_bravo.py"),
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
        ("tests/test_alpha.py", "tests/test_bravo.py"),
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


