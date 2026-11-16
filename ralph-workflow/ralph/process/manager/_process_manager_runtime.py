from __future__ import annotations

from typing import TYPE_CHECKING, Final

import psutil
from loguru import logger

from ralph.process.manager._process_status import ProcessStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ralph.process.manager._process_event import ProcessEvent
    from ralph.process.manager._process_manager_types import _PsutilModuleLike, _PsutilProcessLike


EXPECTED_EXIT_CODES: Final[Mapping[str, frozenset[int]]] = {
    "check-merge-head": frozenset({1}),
    "check_merge_head": frozenset({1}),
    "commit:head-symbolic-ref": frozenset({1}),
    "git-branch-exists": frozenset({1}),
    "git-branch-sha": frozenset({1}),
    "git-is-ancestor": frozenset({1}),
    "git-observe-target-sha": frozenset({1}),
    "git-ort-residue-label": frozenset({1}),
    "git-ort-residue-tracked": frozenset({1}),
    "git-rebase:merge-base": frozenset({1}),
    "git-rebase-head-sha": frozenset({128}),
    "git-remote-tracking-sha-*": frozenset({1}),
    "git-rev-parse-commit-parent": frozenset({128}),
    "git-rev-parse-head": frozenset({128}),
    "git-rev-parse-local-branch": frozenset({128}),
    "git-rev-parse-remote-tracking": frozenset({128}),
    "git-stray-tracked": frozenset({1}),
    "git-symbolic-ref": frozenset({1}),
    "git-symbolic-ref-head": frozenset({1}),
    "hooks-config": frozenset({1}),
    "integration-ladder:ref-readable": frozenset({1}),
    "integration-observation-head": frozenset({1}),
    "integration-observation-merge-base": frozenset({1}),
    "integration_diagnostic:check_ancestor": frozenset({1}),
    "integration_diagnostic:check_merge_head": frozenset({1}),
    "integration_diagnostic:check_rebase_head": frozenset({1}),
    "integration_diagnostic:diff_head": frozenset({1}),
    "integration_diagnostic:gitdir": frozenset({1}),
    "integration_diagnostic:head_parents": frozenset({1}),
    "is-ancestor": frozenset({1}),
    "landed-predicate:patch-id": frozenset({1}),
    "landed-predicate:read": frozenset({128}),
    "operation_evidence:merge_object_resolvable": frozenset({1}),
    "operation_evidence:rebase_object_resolvable": frozenset({1}),
    "pending-merge:identity": frozenset({128}),
    "planning-residue:ref-exists": frozenset({1}),
    "recovery:head-symbolic-ref": frozenset({1}),
    "repair:commit-controls": frozenset({1}),
    "repair:proposed-path": frozenset({1}),
    "symbolic-ref": frozenset({1}),
}

_NO_EXPECTED_EXIT_CODES: Final[frozenset[int]] = frozenset()


def expected_exit_codes(label: str | None) -> frozenset[int]:
    if not label:
        return _NO_EXPECTED_EXIT_CODES
    exact = EXPECTED_EXIT_CODES.get(label)
    if exact is not None:
        return exact
    prefixes = (
        (pattern[:-1], codes)
        for pattern, codes in EXPECTED_EXIT_CODES.items()
        if pattern.endswith("*")
    )
    return next(
        (codes for prefix, codes in prefixes if label.startswith(prefix)),
        _NO_EXPECTED_EXIT_CODES,
    )


class _PsutilProcessAdapter:
    __slots__ = ("raw",)

    def __init__(self, raw: psutil.Process) -> None:
        self.raw = raw

    @property
    def pid(self) -> int:
        return self.raw.pid

    @property
    def returncode(self) -> int | None:
        raw_rc: object = getattr(self.raw, "returncode", None)
        return int(raw_rc) if isinstance(raw_rc, int) and not isinstance(raw_rc, bool) else None

    @property
    def info(self) -> dict[str, int]:
        return {
            str(key): int(value) for key, value in self.raw.info.items() if isinstance(value, int)
        }

    def children(self, *, recursive: bool = False) -> Sequence[_PsutilProcessLike]:
        return tuple(_PsutilProcessAdapter(proc) for proc in self.raw.children(recursive=recursive))

    def terminate(self) -> None:
        self.raw.terminate()

    def kill(self) -> None:
        self.raw.kill()

    def is_running(self) -> bool:
        return self.raw.is_running()

    def status(self) -> str:
        return self.raw.status()

    def name(self) -> str:
        return self.raw.name()

    def create_time(self) -> float:
        return self.raw.create_time()


class _PsutilModuleAdapter:
    __slots__ = ("AccessDenied", "NoSuchProcess", "Process")

    def __init__(self) -> None:
        self.NoSuchProcess: type[BaseException] = psutil.NoSuchProcess
        self.AccessDenied: type[BaseException] = psutil.AccessDenied
        self.Process: Callable[[int], _PsutilProcessLike] = self.process_from_pid

    def process_from_pid(self, pid: int) -> _PsutilProcessLike:
        return _PsutilProcessAdapter(psutil.Process(pid))

    def pid_exists(self, pid: int) -> bool:
        return psutil.pid_exists(pid)

    def process_iter(self, attrs: Sequence[str] | None = None) -> Sequence[_PsutilProcessLike]:
        selected = tuple(attrs) if attrs else None
        return tuple(_PsutilProcessAdapter(proc) for proc in psutil.process_iter(attrs=selected))

    def wait_procs(
        self,
        procs: Sequence[_PsutilProcessLike],
        timeout: float | None = None,
    ) -> tuple[list[_PsutilProcessLike], list[_PsutilProcessLike]]:
        raw = [proc.raw for proc in procs if isinstance(proc, _PsutilProcessAdapter)]
        gone, alive = psutil.wait_procs(raw, timeout=timeout)
        return (
            [_PsutilProcessAdapter(proc) for proc in gone],
            [_PsutilProcessAdapter(proc) for proc in alive],
        )


def load_psutil_module() -> _PsutilModuleLike | None:
    return _PsutilModuleAdapter()


def redacted_command(command: Sequence[str]) -> str:
    if not command:
        return "<empty>"
    remaining = len(command) - 1
    if remaining == 0:
        return f"{command[0]!r}"
    return f"{command[0]!r} (+{remaining} argument{'s' if remaining != 1 else ''} withheld)"


def loguru_event_listener(event: ProcessEvent) -> None:
    record = event.record
    new_status = event.new_status
    if new_status in (ProcessStatus.SPAWNED, ProcessStatus.RUNNING) or (
        new_status in (ProcessStatus.EXITED, ProcessStatus.KILLED) and record.returncode == 0
    ):
        return

    bound = logger.bind(component="process", pid=record.pid, label=record.label)
    if new_status == ProcessStatus.FAILED:
        bound.error(
            "process {} {} rc={} label={} command={} failure={}",
            record.pid,
            new_status.name,
            record.returncode,
            record.label or "<unlabeled>",
            redacted_command(record.command),
            record.failure_message,
        )
        return
    if new_status == ProcessStatus.EXITED and record.returncode in expected_exit_codes(
        record.label
    ):
        bound.debug(
            "process {} {} rc={} label={} command={} (expected answer)",
            record.pid,
            new_status.name,
            record.returncode,
            record.label or "<unlabeled>",
            redacted_command(record.command),
        )
        return
    bound.warning(
        "process {} {} rc={} label={} command={}",
        record.pid,
        new_status.name,
        record.returncode,
        record.label or "<unlabeled>",
        redacted_command(record.command),
    )


__all__ = [
    "EXPECTED_EXIT_CODES",
    "expected_exit_codes",
    "load_psutil_module",
    "loguru_event_listener",
]
