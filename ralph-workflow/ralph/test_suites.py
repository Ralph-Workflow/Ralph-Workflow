"""Run the maintained pytest verification suite under the current interpreter.

.. note::

    The 60-second ABSOLUTE and IMMUTABLE combined test budget is enforced
    UPSTREAM by ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS`` via cumulative
    ``time.monotonic()`` tracking. This module additionally owns one parent
    deadline across all concurrent file shards. Splitting tests into shards
    does NOT increase either budget.
"""

from __future__ import annotations

import multiprocessing
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


# The 1.0 s per-test ITIMER_REAL budget charges wall clock. Six concurrent
# pytest processes are the measured safe profile: eight can exceed the immutable
# 60-second suite budget. Operators may explicitly override ``PYTEST_WORKERS``
# for a measured environment. This is a concurrency bound, not a budget change:
# the 60.0-second combined budget and 1.0-second per-test timeout remain unchanged.
_PYTEST_SHARD_PROCESS_MANAGER = ProcessManager(
    policy=ProcessManagerPolicy(log_events=False, enable_zombie_reaper=False)
)
_DEFAULT_PYTEST_WORKERS = "auto"
_MIN_PYTEST_WORKERS = 6
_MAX_PYTEST_WORKERS = 6

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
    "tests/test_auto_integrate_remote_sync_default_off.py",
    "tests/test_auto_integrate_remote_sync_config.py",
    "tests/test_auto_integrate_remote_sync_pull.py",
    "tests/test_auto_integrate_remote_sync_push.py",
    "tests/test_auto_integrate_remote_sync_reconcile.py",
    "tests/test_auto_integrate_stateless_seam.py",
    "tests/test_auto_integrate_env_pinning.py",
    "tests/test_auto_integrate_markerless_conflicts.py",
    "tests/test_auto_integrate_non_main_target.py",
    "tests/test_auto_integrate_rung4_self_resume.py",
    "tests/test_auto_integrate_recovery.py",
    "tests/test_auto_integrate_recovery_terminal.py",
    "tests/test_auto_integrate_race.py",
    "tests/test_auto_integrate_worktree_sync.py",
    "tests/test_auto_integrate_catalog_e2e.py",
    "tests/test_cli_commit_command.py",
    "tests/test_pi_mcp_extension_sse_behavior.py",
)
_VERIFICATION_MARK_EXPRESSION = "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e"
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
_SHARD_TERMINATION_DRAIN_SECONDS = 1.0
_REQUIRED_E2E_WEIGHT_MULTIPLIER = 120
_TEST_DEFINITION_PATTERN = re.compile(r"^\s*(?:async\s+)?def\s+test_", re.MULTILINE)

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
    raw = os.getenv("PYTEST_WORKERS", _DEFAULT_PYTEST_WORKERS)
    if raw != "auto":
        return raw
    try:
        cores = multiprocessing.cpu_count()
    except Exception:
        return str(_MIN_PYTEST_WORKERS)
    return str(min(max(cores, _MIN_PYTEST_WORKERS), _MAX_PYTEST_WORKERS))


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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            label="verify:pytest-shard",
        ),
    )


def _shard_command(files: Sequence[str], *, basetemp: Path) -> tuple[str, ...]:
    """Build the pytest command for one shard.

    DA-003 (wt-028-display P1 / AC-08 / S-13): the shard
    command opts out of pytest-cache writes
    (``-p no:cacheprovider``) and header output
    (``--no-header``) so the per-shard overhead of writing the
    ``tests/.pytest_cache`` directory and emitting the platform/
    rootdir banner is bounded. On a 12-shard fan-out the
    cache-write + banner path adds 100-300 ms of wall time per
    shard; eliminating it on every shard keeps the slowest shard
    well under the combined 60-second budget. The cache is
    still honored for the unpartitioned ``make test-unit`` /
    ``make test-subprocess-e2e`` targets because those use the
    default xdist path and the cache write does not block the
    wall clock there.
    """
    return (
        sys.executable,
        "-m",
        "pytest",
        *files,
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        "-m",
        _VERIFICATION_MARK_EXPRESSION,
        "--basetemp",
        str(basetemp),
    )


def discover_test_files(cwd: Path) -> tuple[str, ...]:
    """Return sorted pytest files using pytest's default filename patterns."""
    tests_root = cwd / "tests"
    selected_files = {
        path.relative_to(cwd).as_posix()
        for pattern in ("test_*.py", "*_test.py")
        for path in tests_root.rglob(pattern)
        if path.is_file()
    }
    if not selected_files:
        raise RuntimeError(f"static pytest discovery selected no files under {tests_root}")
    discovered = tuple(sorted(selected_files))
    validate_required_auto_integrate_selection(discovered)
    return discovered


def estimate_test_file_weight(source: str) -> int:
    """Estimate collection work from statically visible test definitions."""
    return max(1, sum(1 for _match in _TEST_DEFINITION_PATTERN.finditer(source)))


def _test_file_weight(cwd: Path, relative_path: str) -> int:
    source = (cwd / relative_path).read_text(encoding="utf-8")
    weight = estimate_test_file_weight(source)
    if relative_path in REQUIRED_AUTO_INTEGRATE_E2E_FILES:
        return weight * _REQUIRED_E2E_WEIGHT_MULTIPLIER
    return weight


def _remaining_seconds(deadline: float, monotonic: Callable[[], float]) -> float:
    return max(0.0, deadline - monotonic())


def _decode_output(output: bytes | None) -> str:
    return output.decode("utf-8", errors="replace") if output else ""


def _reap_process(
    process: ShardProcess,
    *,
    timeout_seconds: float,
) -> tuple[str, str]:
    process.cleanup_orphans()
    try:
        stdout, stderr = process.communicate(timeout=max(0.0, timeout_seconds))
    except subprocess.TimeoutExpired:
        return "", "pytest shard did not exit after termination\n"
    return _decode_output(stdout), _decode_output(stderr)


def _terminate_and_reap(
    processes: Sequence[ShardProcess],
    *,
    deadline: float,
    monotonic: Callable[[], float],
) -> tuple[tuple[str, str], ...]:
    for process in processes:
        if process.poll() is None:
            process.terminate(grace_period_s=0.0)
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


def _run_shards(
    shards: Sequence[Sequence[str]],
    *,
    cwd: Path,
    env: Mapping[str, str],
    basetemp_root: Path,
    deadline: float,
    spawner: ShardSpawner,
    monotonic: Callable[[], float],
    wait: Callable[[float], None],
) -> int:
    processes: list[ShardProcess] = []
    try:
        for shard_index, shard in enumerate(shards):
            if _remaining_seconds(deadline, monotonic) <= 0:
                outputs = _terminate_and_reap(
                    processes,
                    deadline=deadline,
                    monotonic=monotonic,
                )
                _print_shard_outputs(outputs)
                return TIMEOUT_EXIT_CODE
            processes.append(
                spawner(
                    _shard_command(
                        shard,
                        basetemp=basetemp_root / f"shard-{shard_index}",
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
                continue
            completed[index] = _reap_process(
                process,
                timeout_seconds=_remaining_seconds(deadline, monotonic),
            )
            if returncode != 0:
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
    return 0


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
    deadline = monotonic() + suite_timeout_seconds
    env = build_timeout_env(
        test_timeout_seconds=timeout_seconds_from_env(
            TEST_TIMEOUT_ENV, DEFAULT_TEST_TIMEOUT_SECONDS
        ),
        suite_timeout_seconds=suite_timeout_seconds,
    )
    if auto_integrate_e2e_only:
        selected_files = REQUIRED_AUTO_INTEGRATE_E2E_FILES
    else:
        selected_files = file_discoverer(cwd)
        validate_required_auto_integrate_selection(selected_files)

    shards = partition_selected_files(
        selected_files,
        worker_count=int(_pytest_workers()),
        file_weights={path: file_weigher(cwd, path) for path in selected_files},
    )
    validate_exact_file_assignment(selected_files, shards)
    profile = "auto-integrate-e2e" if auto_integrate_e2e_only else "verification"
    basetemp_parent = Path(tempfile.gettempdir()) / "ralph-pytest-shards"
    basetemp_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"{profile}-",
        dir=basetemp_parent,
    ) as basetemp_root:
        return _run_shards(
            shards,
            cwd=cwd,
            env=env,
            basetemp_root=Path(basetemp_root),
            deadline=deadline,
            spawner=spawner,
            monotonic=monotonic,
            wait=wait,
        )


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
    if arguments:
        raise SystemExit("ralph.test_suites accepts only the optional --auto-integrate-e2e profile")
    return run_test_suites(cwd=Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
