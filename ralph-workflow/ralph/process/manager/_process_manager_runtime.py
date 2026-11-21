from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

import psutil
from loguru import logger

from ralph._typed_attrs import read_attr
from ralph.process.manager._process_status import ProcessStatus

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ralph.process.manager._process_event import ProcessEvent
    from ralph.process.manager._process_manager_types import _PsutilModuleLike, _PsutilProcessLike

type ExitCodeLevel = Literal["DEBUG", "WARNING"]


EXPECTED_EXIT_CODES: Final[Mapping[str, frozenset[int]]] = {
    "auto-integrate:stale-snapshot-head-tree": frozenset({128}),
    "auto-integrate:stale-snapshot-nested-head": frozenset({128}),
    "check-merge-head": frozenset({1}),
    "check_merge_head": frozenset({1}),
    "check-git-worktree": frozenset({128}),
    "commit:branch-tip-sha": frozenset({128}),
    "commit:branch-tip-sha-read": frozenset({128}),
    "commit:detached-head-sha": frozenset({128}),
    "commit:head-symbolic-ref": frozenset({1}),
    "git-branch-exists": frozenset({1}),
    "git-branch-sha": frozenset({1}),
    "git-common-dir": frozenset({128}),
    "git-detached-head-branch-tip": frozenset({128}),
    "git-hooks-path": frozenset({128}),
    "publication-gate-tree": frozenset({128}),
    "publication-gate-syntax": frozenset({2}),
    "git-is-ancestor": frozenset({1}),
    "git-merge-progress:git-dir": frozenset({128}),
    "git-observe-target-sha": frozenset({1}),
    "git-ort-residue-label": frozenset({1}),
    "git-ort-residue-tracked": frozenset({1}),
    "git-rebase:merge-base": frozenset({1}),
    "git-rebase:set-aside-path": frozenset({128}),
    "git-rebase-head-sha": frozenset({128}),
    "git-rebase-head-subject": frozenset({128}),
    "git-rebase-progress-path": frozenset({128}),
    "git-recovery-git-dir": frozenset({128}),
    "git-remote-tracking-sha-*": frozenset({1}),
    "git-rev-list": frozenset({128}),
    "git-rev-parse-commit-parent": frozenset({128}),
    "git-rev-parse-head": frozenset({128}),
    "git-rev-parse-head-tree": frozenset({128}),
    "git-rev-parse-head-verify": frozenset({128}),
    "git-rev-parse-local-branch": frozenset({128}),
    "git-rev-parse-remote-tracking": frozenset({128}),
    "git-staged-marker-line": frozenset({128}),
    "git-stray-tracked": frozenset({1}),
    "git-symbolic-ref": frozenset({1}),
    "git-symbolic-ref-head": frozenset({1}),
    "hooks-config": frozenset({1}),
    "integration-ladder:env-probe": frozenset({128}),
    "integration-ladder:fresh-tip": frozenset({128}),
    "integration-ladder:fresh-tip-for-repoint": frozenset({128}),
    "integration-ladder:head-sha": frozenset({128}),
    "integration-ladder:ref-readable": frozenset({1}),
    "integration-ladder:repoint-detached-probe": frozenset({128}),
    "integration-ladder:rev-parse--git-common-dir": frozenset({128}),
    "integration-ladder:rev-parse--git-dir": frozenset({128}),
    "integration-ladder:target-sha": frozenset({128}),
    "integration-ladder:write-patch": frozenset({128}),
    "integration-observation-git-dir": frozenset({128}),
    "integration-observation-head": frozenset({1}),
    "integration-observation-merge-base": frozenset({1}),
    "integration-preservation:intent": frozenset({128}),
    "integration-preservation:read-operation-tip": frozenset({128}),
    "integration-preservation:work-diff": frozenset({128}),
    "integration:lock-dir": frozenset({128}),
    "integration_diagnostic:check_ancestor": frozenset({1}),
    "integration_diagnostic:check_merge_head": frozenset({1}),
    "integration_diagnostic:check_rebase_head": frozenset({1}),
    "integration_diagnostic:diff_head": frozenset({1, 128}),
    "integration_diagnostic:gitdir": frozenset({1, 128}),
    "integration_diagnostic:head_parents": frozenset({1, 128}),
    "is-ancestor": frozenset({1}),
    "landed-predicate:patch-id": frozenset({1}),
    "landed-predicate:read": frozenset({128}),
    "operation_evidence:gitdir": frozenset({128}),
    "operation_evidence:head": frozenset({128}),
    "operation_evidence:merge_object_resolvable": frozenset({1}),
    "operation_evidence:rebase_object_resolvable": frozenset({1}),
    "pending-merge:identity": frozenset({128}),
    "planning-residue:head": frozenset({128}),
    "planning-residue:incoming-overwrite": frozenset({128}),
    "planning-residue:preserve-tree": frozenset({128}),
    "planning-residue:ref-exists": frozenset({1}),
    "planning-residue:tracked-diff": frozenset({128}),
    "recovery-git-dir": frozenset({128}),
    "recovery:branch-tip": frozenset({128}),
    "recovery:branch-verify": frozenset({128}),
    "recovery:completed-rebase-branch": frozenset({1}),
    "recovery:feature-tip-exists": frozenset({1}),
    "recovery:head-rev-parse": frozenset({128}),
    "recovery:head-symbolic-ref": frozenset({1}),
    "recovery:unowned-merge-identity": frozenset({128}),
    "repair:baseline-path": frozenset({1, 128}),
    "repair:commit-controls": frozenset({1}),
    "repair:proposed-path": frozenset({1}),
    "symbolic-ref": frozenset({1}),
}

_NO_EXPECTED_EXIT_CODES: Final[frozenset[int]] = frozenset()

BENIGN_COMMAND_CLASSES: Final[Mapping[str, frozenset[int]]] = {
    "git symbolic-ref --quiet": frozenset({1}),
    "git merge-base --is-ancestor": frozenset({1}),
    "git rev-parse --verify --quiet MERGE_HEAD": frozenset({1}),
}

_GIT_OPTIONS_WITH_VALUE: Final[frozenset[str]] = frozenset(
    {
        "--exec-path",
        "--git-dir",
        "--namespace",
        "--work-tree",
        "-c",
    }
)


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


def _git_probe_class(command: Sequence[str]) -> str | None:
    if not command or Path(command[0]).name != "git":
        return None
    args = command[1:]
    flags: set[str] = set()
    subcommand = ""
    index = 0
    while index < len(args):
        token = args[index]
        if token in _GIT_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token.startswith("-"):
            flags.add(token)
            index += 1
            continue
        if not subcommand:
            subcommand = token
        index += 1
    if subcommand == "symbolic-ref" and "--quiet" in flags:
        return "git symbolic-ref --quiet"
    if subcommand == "merge-base" and "--is-ancestor" in flags:
        return "git merge-base --is-ancestor"
    if (
        subcommand == "rev-parse"
        and "--verify" in flags
        and "--quiet" in flags
        and any(token.startswith("MERGE_HEAD") for token in args)
    ):
        return "git rev-parse --verify --quiet MERGE_HEAD"
    return None


def benign_command_class_exit_codes(command: Sequence[str]) -> frozenset[int]:
    probe_class = _git_probe_class(command)
    if probe_class is None:
        return _NO_EXPECTED_EXIT_CODES
    return BENIGN_COMMAND_CLASSES[probe_class]


def exit_code_level(
    label: str | None,
    command: Sequence[str],
    returncode: int | None,
) -> ExitCodeLevel:
    if returncode is None:
        return "WARNING"
    label_codes = expected_exit_codes(label)
    if not label_codes:
        return "WARNING"
    if returncode in label_codes:
        return "DEBUG"
    if returncode in benign_command_class_exit_codes(command):
        return "DEBUG"
    return "WARNING"


class _PsutilProcessAdapter:
    __slots__ = ("raw",)

    def __init__(self, raw: psutil.Process) -> None:
        self.raw = raw

    @property
    def pid(self) -> int:
        return self.raw.pid

    @property
    def returncode(self) -> int | None:
        raw_rc: object = read_attr(self.raw, "returncode", None)
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
    if new_status == ProcessStatus.EXITED and (
        exit_code_level(record.label, record.command, record.returncode) == "DEBUG"
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
    "BENIGN_COMMAND_CLASSES",
    "EXPECTED_EXIT_CODES",
    "benign_command_class_exit_codes",
    "exit_code_level",
    "expected_exit_codes",
    "load_psutil_module",
    "loguru_event_listener",
]
