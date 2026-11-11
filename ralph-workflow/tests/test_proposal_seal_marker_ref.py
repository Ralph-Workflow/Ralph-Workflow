from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git.exc import GitCommandError
from loguru import logger

from ralph.git.git_command_queue import route_git_method
from ralph.git.proposal_seal import (
    REJECTED_PROPOSAL_REF_PREFIX,
    Seal,
    rollback,
    seal_pending,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ralph.git.git_command_queue import GitMethodRepo

_RUN_ID = "run-marker"
_ITERATION = 1


class _MarkerRefProbe:
    def __init__(self, proposal_ref: str, answer: Callable[[], object] | None = None) -> None:
        self._proposal_ref = proposal_ref
        self._answer = answer
        self._marker_phase = False
        self.resolved_sha_calls = 0
        self.marker_ref_writes: list[tuple[str, object]] = []

    def arm_marker_phase(self, step: str) -> None:
        if step == "complete":
            self._marker_phase = True

    def __call__(
        self,
        repo: GitMethodRepo,
        method_name: str,
        args: Sequence[object] = (),
        kwargs: Mapping[str, object] | None = None,
    ) -> object:
        if (
            self._answer is not None
            and self._marker_phase
            and method_name == "rev_parse"
            and tuple(args) == ("--verify", self._proposal_ref)
        ):
            self.resolved_sha_calls += 1
            return self._answer()
        if method_name == "update_ref" and args:
            ref_name = str(args[0])
            if ref_name.startswith(REJECTED_PROPOSAL_REF_PREFIX):
                self.marker_ref_writes.append((ref_name, args[1] if len(args) > 1 else None))
        return route_git_method(repo, method_name, args, kwargs)


def _dirty_proposal(repo_path: Path) -> tuple[Seal, Path]:
    tracked = repo_path / "README.md"
    tracked.write_text("modified by the proposal\n", encoding="utf-8")
    added = repo_path / "added.txt"
    added.write_text("added by the proposal\n", encoding="utf-8")
    seal = seal_pending(repo_path, _RUN_ID, _ITERATION)
    return seal, added


def _marker_ref_path(repo_path: Path) -> Path:
    marker_ref = f"{REJECTED_PROPOSAL_REF_PREFIX}{_RUN_ID}/{_ITERATION}"
    return repo_path / ".git" / marker_ref


def _warning_sink() -> tuple[list[str], int]:
    records: list[str] = []
    return records, logger.add(records.append, level="WARNING", format="{message}")


def test_marker_ref_is_not_written_when_the_resolved_sha_is_not_an_object_name(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = tmp_git_repo
    seal, added = _dirty_proposal(repo_path)

    probe = _MarkerRefProbe(seal.ref, lambda: "not-an-object-name\n")
    monkeypatch.setattr("ralph.git.proposal_seal.route_git_method", probe)

    warnings, sink_id = _warning_sink()
    try:
        rollback(repo_path, seal.ref, step_hook=probe.arm_marker_phase)
    finally:
        logger.remove(sink_id)

    assert probe.resolved_sha_calls == 1
    assert probe.marker_ref_writes == []
    assert not _marker_ref_path(repo_path).exists()
    assert not added.exists()
    assert any(seal.ref in message for message in warnings)


def test_marker_ref_is_not_written_when_git_fails_to_resolve_the_proposal(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def _fail() -> object:
        raise GitCommandError(
            ["git", "rev-parse", "--verify", "refs/ralph/proposals"],
            128,
            "fatal: ambiguous argument",
        )

    repo_path = tmp_git_repo
    seal, added = _dirty_proposal(repo_path)

    probe = _MarkerRefProbe(seal.ref, _fail)
    monkeypatch.setattr("ralph.git.proposal_seal.route_git_method", probe)

    warnings, sink_id = _warning_sink()
    try:
        rollback(repo_path, seal.ref, step_hook=probe.arm_marker_phase)
    finally:
        logger.remove(sink_id)

    assert probe.resolved_sha_calls == 1
    assert probe.marker_ref_writes == []
    assert not _marker_ref_path(repo_path).exists()
    assert not added.exists()
    assert any(seal.ref in message for message in warnings)


def test_marker_ref_is_written_when_the_resolved_sha_is_an_object_name(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_path = tmp_git_repo
    seal, added = _dirty_proposal(repo_path)

    probe = _MarkerRefProbe(seal.ref)
    monkeypatch.setattr("ralph.git.proposal_seal.route_git_method", probe)

    rollback(repo_path, seal.ref, step_hook=probe.arm_marker_phase)

    marker_ref = f"{REJECTED_PROPOSAL_REF_PREFIX}{_RUN_ID}/{_ITERATION}"
    assert probe.marker_ref_writes == [(marker_ref, seal.commit_sha)]
    assert _marker_ref_path(repo_path).is_file()
    assert not added.exists()
