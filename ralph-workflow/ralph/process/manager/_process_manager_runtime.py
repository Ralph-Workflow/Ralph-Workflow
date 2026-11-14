from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph.process.manager._process_status import ProcessStatus

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from ralph.process.manager._process_event import ProcessEvent
    from ralph.process.manager._process_manager_types import _PsutilModuleLike, _PsutilProcessLike


EXPECTED_EXIT_CODES: Final[Mapping[str, frozenset[int]]] = {
    "commit:head-symbolic-ref": frozenset({1}),
    "git-branch-exists": frozenset({1}),
    "git-branch-sha": frozenset({1}),
    "git-gitlink-ancestor": frozenset({1}),
    "git-gitlink-object": frozenset({1}),
    "git-is-ancestor": frozenset({1}),
    "git-observe-target-sha": frozenset({1}),
    "git-ort-residue-label": frozenset({1}),
    "git-ort-residue-tracked": frozenset({1}),
    "git-rebase:merge-base": frozenset({1}),
    "git-remote-tracking-sha-*": frozenset({1}),
    "git-rev-parse-commit-parent": frozenset({128}),
    "git-rev-parse-head": frozenset({128}),
    "git-rev-parse-local-branch": frozenset({128}),
    "git-rev-parse-remote-tracking": frozenset({128}),
    "git-stray-tracked": frozenset({1}),
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
    "operation_evidence:merge_object_resolvable": frozenset({1}),
    "operation_evidence:rebase_object_resolvable": frozenset({1}),
    "planning-residue:ref-exists": frozenset({1}),
    "recovery:head-symbolic-ref": frozenset({1}),
    "repair:commit-controls": frozenset({1}),
    "repair:proposed-path": frozenset({1}),
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


class _PsutilModuleAdapter:
    def __init__(self, module: _PsutilModuleLike) -> None:
        self._module = module

    def process_from_pid(self, pid: int) -> _PsutilProcessLike:
        return self._module.Process(pid)

    def __getattr__(self, name: str) -> object:
        return cast("object", getattr(self._module, name))


def load_psutil_module() -> _PsutilModuleLike | None:
    try:
        psutil_import = importlib.import_module("psutil")
    except ModuleNotFoundError:
        return None
    return cast("_PsutilModuleLike", _PsutilModuleAdapter(cast("_PsutilModuleLike", psutil_import)))


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
