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
# shard; the CPU-capped shard count is selected below and operators may
# override ``PYTEST_WORKERS`` for smaller hosts.
_PYTEST_SHARD_PROCESS_MANAGER = ProcessManager(
    policy=ProcessManagerPolicy(log_events=False, enable_zombie_reaper=False)
)
_DEFAULT_PYTEST_WORKERS = "auto"
# Hard cap on the number of plain-pytest shards; raising this cap does NOT
# raise the combined 60-second budget tracked upstream in
# ``ralph/verify.py:_TOTAL_TEST_BUDGET_SECONDS``. Lowering it widens each
# shard's work (more files per shard) and is therefore a budget-pressure
# change, not a budget-relief change.
_MAX_PYTEST_WORKERS = 32
# Default in-shard xdist worker count is ``"0"`` (plain pytest per shard)
# because on the maintained 32-core CI profile the shard-saturated
# 16-shard fan-out already uses one pytest process per shard and adding
# xdist workers inside each shard shifts wall-clock budget from
# parallel-IO back into pytest-coordination overhead. Operators may
# override with ``PYTEST_XDIST_WORKERS_PER_SHARD=auto`` for the legacy
# CPU-utilisation-maximising policy or an explicit integer for a custom
# in-shard fan-out.
_DEFAULT_XDIST_WORKERS_PER_SHARD = "0"
# Hard cap on the number of pytest-xdist workers spawned INSIDE each shard.
# Combined with ``_MAX_PYTEST_WORKERS`` (32 shards) this gives a maximum
# fan-out of 32 * 4 = 128 workers when ``_DEFAULT_XDIST_WORKERS_PER_SHARD``
# is overridden to ``"auto"`` or a positive integer. The default plain-
# pytest path keeps the slowest shard under the 60s combined budget on
# 32-core CI without coordination overhead.
_MAX_XDIST_WORKERS_PER_SHARD = 4

#: Exact subprocess-E2E files required by the authoritative verification
#: profile. This registry also drives the focused Make target, so the two
#: selections cannot drift.
REQUIRED_AUTO_INTEGRATE_E2E_FILES: tuple[str, ...] = (
    # One real-git landing journey retains the external Git boundary proof;
    # decision and recovery variants run in the opt-in subprocess profile.
    "tests/test_auto_integrate_end_to_end.py",
)
_VERIFICATION_MARK_EXPRESSION = "(not subprocess_e2e and not smoke) or required_auto_integrate_e2e"
_SUBPROCESS_E2E_MARK_EXPRESSION = (
    "subprocess_e2e and not smoke and not live_agy and not verify_budget_real_time"
)
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
_PARAMETRIZE_CASES_ARGUMENT_INDEX = 1

if not REQUIRED_AUTO_INTEGRATE_E2E_FILES:
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not be empty")
if len(REQUIRED_AUTO_INTEGRATE_E2E_FILES) != len(set(REQUIRED_AUTO_INTEGRATE_E2E_FILES)):
    raise RuntimeError("REQUIRED_AUTO_INTEGRATE_E2E_FILES must not contain duplicates")
if _MAX_PYTEST_WORKERS <= 0:
    raise RuntimeError("_MAX_PYTEST_WORKERS must be positive")
if _MAX_XDIST_WORKERS_PER_SHARD < 0:
    raise RuntimeError("_MAX_XDIST_WORKERS_PER_SHARD must be non-negative")


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
    """Return an explicit override or the CPU-capped verified shard profile."""
    raw = os.getenv("PYTEST_WORKERS", _DEFAULT_PYTEST_WORKERS)
    if raw != "auto":
        return raw
    available_cores = os.cpu_count() or 2
    return str(max(1, min(_MAX_PYTEST_WORKERS, available_cores - 1)))


def _xdist_workers_per_shard() -> str:
    """Return the in-shard xdist worker count, or ``"0"`` to disable xdist.

    The default is ``"0"`` (plain pytest per shard), which on the maintained
    32-core CI profile keeps the slowest shard well under the 60-second
    combined budget because the shard-saturated 16-shard fan-out already
    uses one pytest process per shard and adding xdist workers inside each
    shard trades parallel IO for pytest-coordination overhead. Operators
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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            label="verify:pytest-shard",
        ),
    )


def _shard_command(
    files: Sequence[str],
    *,
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
    rootdir banner is bounded. On a 12-shard fan-out the
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
        *files,
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        *xdist_args,
        "-m",
        marker_expression,
        "--basetemp",
        str(basetemp),
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


def discover_test_files(cwd: Path) -> tuple[str, ...]:
    """Return verification files, excluding only statically proven E2E-only modules.

    Avoid parsing the common non-E2E case: a module can only be E2E-only when
    it assigns ``pytestmark`` and mentions ``subprocess_e2e``.  This keeps
    discovery below the one-second per-test ceiling while preserving the
    conservative AST classification for every possible E2E candidate.
    """
    discovered: list[str] = []
    for path in _discover_all_test_files(cwd):
        if path in REQUIRED_AUTO_INTEGRATE_E2E_FILES:
            discovered.append(path)
            continue
        source_bytes = (cwd / path).read_bytes()
        if b"pytestmark" not in source_bytes or b"subprocess_e2e" not in source_bytes:
            discovered.append(path)
            continue
        source = source_bytes.decode("utf-8")
        if not _is_module_subprocess_e2e(source, filename=path):
            discovered.append(path)
    selected = tuple(discovered)
    validate_required_auto_integrate_selection(selected)
    return selected


def discover_subprocess_e2e_files(cwd: Path) -> tuple[str, ...]:
    """Return test files that statically reference the subprocess-E2E marker.

    Supplying explicit files avoids pytest collecting the entire repository
    merely to deselect non-E2E tests. Pytest still applies the canonical marker
    expression within each selected file, preserving function-level exclusions.
    """
    selected: list[str] = []
    for relative_path in _discover_all_test_files(cwd):
        source = (cwd / relative_path).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative_path)
        if any(
            isinstance(node, ast.Attribute)
            and node.attr == "subprocess_e2e"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "mark"
            and isinstance(node.value.value, ast.Name)
            and node.value.value.id == "pytest"
            for node in ast.walk(tree)
        ):
            selected.append(relative_path)
    if not selected:
        raise RuntimeError("static subprocess-E2E discovery selected no files")
    return tuple(selected)


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
    """
    tree = ast.parse(source)
    weight = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or not node.name.startswith(
            "test_"
        ):
            continue
        multiplier = 1
        for decorator in node.decorator_list:
            multiplier *= _literal_parametrize_case_count(decorator)
        weight += multiplier
    return max(1, weight)


def _test_file_weight(cwd: Path, relative_path: str) -> int:
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
    process.cleanup_orphans()
    try:
        stdout, stderr = process.communicate(timeout=max(0.0, timeout_seconds))
    except subprocess.TimeoutExpired:
        if timeout_seconds <= 0.0:
            return "", ""
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
            processes.append(
                spawner(
                    _shard_command(
                        shard,
                        basetemp=basetemp_root / f"shard-{shard_index}",
                        marker_expression=marker_expression,
                        xdist_workers=xdist_workers,
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
    subprocess_e2e_only: bool = False,
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
    if auto_integrate_e2e_only and subprocess_e2e_only:
        raise ValueError("test-suite profiles are mutually exclusive")
    marker_expression = _VERIFICATION_MARK_EXPRESSION
    if auto_integrate_e2e_only:
        selected_files = REQUIRED_AUTO_INTEGRATE_E2E_FILES
    elif subprocess_e2e_only:
        selected_files = discover_subprocess_e2e_files(cwd)
        marker_expression = _SUBPROCESS_E2E_MARK_EXPRESSION
    else:
        selected_files = file_discoverer(cwd)
        validate_required_auto_integrate_selection(selected_files)

    shards = partition_selected_files(
        selected_files,
        worker_count=int(_pytest_workers()),
        file_weights={path: file_weigher(cwd, path) for path in selected_files},
    )
    validate_exact_file_assignment(selected_files, shards)
    if auto_integrate_e2e_only:
        profile = "auto-integrate-e2e"
    elif subprocess_e2e_only:
        profile = "subprocess-e2e"
    else:
        profile = "verification"
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
            started_at=started_at,
            spawner=spawner,
            monotonic=monotonic,
            wait=wait,
            marker_expression=marker_expression,
            xdist_workers=_xdist_workers_per_shard(),
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
    if arguments == ("--subprocess-e2e",):
        return run_test_suites(cwd=Path.cwd(), subprocess_e2e_only=True)
    if arguments:
        raise SystemExit("ralph.test_suites accepts only --auto-integrate-e2e or --subprocess-e2e")
    return run_test_suites(cwd=Path.cwd())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
