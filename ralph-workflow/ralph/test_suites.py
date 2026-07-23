"""Run the maintained pytest verification suite under the current interpreter.

.. note::

    The 60-second ABSOLUTE and IMMUTABLE combined test budget is enforced
    UPSTREAM by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`` via cumulative
    ``time.monotonic()`` tracking, not by this module. This module provides
    per-suite timeout wrapping only. Splitting tests into more suites or
    adding new test targets does NOT increase the combined budget.
"""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.verify_timeout import (
    DEFAULT_SUITE_TIMEOUT_SECONDS,
    DEFAULT_TEST_TIMEOUT_SECONDS,
    TEST_TIMEOUT_ENV,
    SuiteTimeoutError,
    build_timeout_env,
    run_command_with_timeout,
    timeout_seconds_from_env,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence
    from typing import Protocol

    from ralph.executor.process import ProcessResult

    class SuiteRunner(Protocol):
        """Subprocess seam for the maintained pytest suite.

        Owns the full lifecycle of a single pytest invocation —
        spawning the subprocess, enforcing the suite wall-clock cap,
        returning a ``ProcessResult``. Default implementation is
        ``_default_runner``, which delegates to
        :func:`ralph.verify_timeout.run_command_with_timeout`.
        """

        def __call__(
            self,
            command: Sequence[str],
            *,
            cwd: Path,
            env: Mapping[str, str] | None = None,
            suite_timeout_seconds: float,
        ) -> ProcessResult: ...


# Default: capped ``auto``. The 1.0 s per-test ITIMER_REAL budget charges
# wall clock, so the worker count has both a floor and a ceiling: too few
# workers and the suite runs past the immutable 60-second combined budget,
# too many and a trivial test is starved past its own 1 s cap.
#
# The cap was 2 when the suite held ~11.8k tests. At 12,137 collected tests
# that no longer clears the floor -- measured on a 12-core host, two workers
# need ~160 s and four reach only ~50 % of the suite before the 60 s cap
# fires. Eight workers complete the same suite (12111 passed, 26 skipped)
# in 38.09 s with zero per-test SIGALRM failures, so the cap is 8.
# ``worksteal`` scheduling balances the slowest files (the multi-second
# integration / recovery / pipeline-runner tests) across workers once the
# fast files drain, keeping wall-clock under the immutable 60 s budget;
# ``loadfile`` pinned a file to one worker and could not rebalance the
# slowest files, pushing the suite past 60 s under contention. This
# matches the ``make test-unit`` target's scheduling, so both targets now
# agree.
#
# This is a concurrency cap, not a budget change:
# ``_TOTAL_TEST_BUDGET_SECONDS`` (60.0) and
# ``DEFAULT_TEST_TIMEOUT_SECONDS`` (1.0) are unchanged. Override via
# the ``PYTEST_WORKERS`` env var if needed. Note that ``make test`` exports
# ``PYTEST_WORKERS`` from the Makefile, so this cap governs only the
# ``auto`` path (a bare ``python -m ralph.test_suites`` with no env var).
_DEFAULT_PYTEST_WORKERS = "auto"
_MAX_PYTEST_WORKERS = 8

#: Exact subprocess-E2E files required by the authoritative verification
#: profile. This registry also drives the focused Make target, so the two
#: selections cannot drift.
REQUIRED_AUTO_INTEGRATE_E2E_FILES: tuple[str, ...] = (
    "tests/test_auto_integrate_conflict_e2e.py",
    "tests/test_auto_integrate_clone_conflict_e2e.py",
    "tests/test_auto_integrate_catchup_e2e.py",
    "tests/test_auto_integrate_worktree_prefix_e2e.py",
    "tests/test_auto_integrate_fail_closed_e2e.py",
    "tests/test_auto_integrate_end_to_end.py",
    "tests/test_auto_integrate_refresh_contract.py",
    "tests/test_auto_integrate_seams_e2e.py",
    "tests/test_auto_integrate_conflict_seams_e2e.py",
    "tests/test_auto_integrate_rebase_conflict_e2e.py",
    "tests/test_auto_integrate_real_agent_resolution_e2e.py",
    "tests/test_auto_integrate_fleet_conflict_e2e.py",
    "tests/test_auto_integrate_local_fleet_target_e2e.py",
    "tests/test_auto_integrate_remote_push.py",
    "tests/test_auto_integrate_remote_refresh.py",
    "tests/test_auto_integrate_stateless_seam.py",
    "tests/test_auto_integrate_env_pinning.py",
    "tests/test_auto_integrate_markerless_conflicts.py",
    "tests/test_auto_integrate_non_main_target.py",
    "tests/test_auto_integrate_rung4_self_resume.py",
    "tests/test_auto_integrate_recovery.py",
    "tests/test_auto_integrate_race.py",
    "tests/test_auto_integrate_worktree_sync.py",
    "tests/test_auto_integrate_catalog_e2e.py",
)
REQUIRED_AUTO_INTEGRATE_SELECTION_ENV = "RALPH_VERIFY_REQUIRED_AUTO_INTEGRATE_E2E"

if not REQUIRED_AUTO_INTEGRATE_E2E_FILES:
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not be empty")
if len(REQUIRED_AUTO_INTEGRATE_E2E_FILES) != len(set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)):
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not contain duplicates")


def validate_required_auto_integrate_selection(selected_files: Iterable[str]) -> None:
    """Fail when combined pytest selection omits a required E2E file."""
    selected = frozenset(selected_files)
    missing = tuple(path for path in REQUIRED_AUTO_INTEGRATE_E2E_FILES if path not in selected)
    if missing:
        raise RuntimeError(
            "combined pytest selection omitted required auto-integrate E2E files: "
            + ", ".join(missing)
        )


def _pytest_workers() -> str:
    raw = os.getenv("PYTEST_WORKERS", _DEFAULT_PYTEST_WORKERS)
    if raw != "auto":
        return raw
    try:
        return str(min(multiprocessing.cpu_count(), _MAX_PYTEST_WORKERS))
    except Exception:
        return str(_MAX_PYTEST_WORKERS)


def _default_runner(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    suite_timeout_seconds: float,
) -> ProcessResult:
    return run_command_with_timeout(
        command,
        cwd=cwd,
        env=env,
        suite_timeout_seconds=suite_timeout_seconds,
        capture_output=False,
    )


def _verification_command() -> tuple[str, ...]:
    workers = _pytest_workers()
    return (
        sys.executable,
        "-m",
        "pytest",
        "tests/",
        "-q",
        "-n",
        workers,
        "--dist",
        "worksteal",
        "-m",
        "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e",
    )


def _auto_integrate_e2e_command() -> tuple[str, ...]:
    workers = _pytest_workers()
    return (
        sys.executable,
        "-m",
        "pytest",
        *REQUIRED_AUTO_INTEGRATE_E2E_FILES,
        "-q",
        "-n",
        workers,
        "--dist",
        "worksteal",
    )


def run_test_suites(
    *,
    cwd: Path,
    suite_timeout_seconds: float = DEFAULT_SUITE_TIMEOUT_SECONDS,
    runner: SuiteRunner = _default_runner,
    auto_integrate_e2e_only: bool = False,
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
        runner: Subprocess seam (``SuiteRunner`` protocol). Defaults
            to ``_default_runner``, which delegates to
            ``run_command_with_timeout``.

    Returns:
        The subprocess returncode. ``0`` on success; non-zero mirrors
        pytest's own exit semantics (test failures, collection
        errors, timeout). ``SuiteTimeoutError`` is converted to a
        ``124`` exit by the underlying runner.

    Side effects:
        Spawns a pytest subprocess via ``runner``. The subprocess
        inherits an environment with
        ``RALPH_PYTEST_TEST_TIMEOUT_SECONDS`` and
        ``RALPH_PYTEST_SUITE_TIMEOUT_SECONDS`` populated.
    """
    env = build_timeout_env(
        test_timeout_seconds=timeout_seconds_from_env(
            TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS
        ),
        suite_timeout_seconds=suite_timeout_seconds,
    )
    if not auto_integrate_e2e_only:
        env[REQUIRED_AUTO_INTEGRATE_SELECTION_ENV] = "1"
    try:
        result = runner(
            _auto_integrate_e2e_command()
            if auto_integrate_e2e_only
            else _verification_command(),
            cwd=cwd,
            env=env,
            suite_timeout_seconds=suite_timeout_seconds,
        )
    except SuiteTimeoutError as exc:
        print(str(exc), file=sys.stderr)
        return 124
    return result.returncode


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``ralph.test_suites`` command-line tool.

    Forwards to :func:`run_test_suites` using the current working
    directory. Returns the pytest subprocess exit code. Positional
    arguments are rejected with ``SystemExit`` to surface silent
    misuse.
    """
    arguments = tuple(argv or ())
    if arguments == ("--auto-integrate-e2e",):
        return run_test_suites(cwd=Path.cwd(), auto_integrate_e2e_only=True)
    if arguments:
        raise SystemExit(
            "ralph.test_suites accepts only the optional --auto-integrate-e2e profile"
        )
    return run_test_suites(cwd=Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
