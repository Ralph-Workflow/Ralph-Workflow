"""Black-box proofs for the rejected-proposal marker ref write.

A rolled-back proposal is marked under
``refs/ralph/rejected-proposals/<run>/<iteration>`` so the commit sweep keeps
the rejected work recoverable through ``git checkout <ref> -- .``.

Two properties are proved here, and both are properties of the EXTERNAL git
boundary rather than of the module's internals:

* the value git resolves the proposal ref to is an UNTRUSTED input, so a
  value that is not a git object name must never be pointed at a ref;
* the mark is a side effect of a rollback that ALREADY restored the tree, so
  a mark that cannot be written stays a nonfatal, logged failure instead of
  turning the rollback into a hard failure.

Every proof drives a real repository inside the pytest ``tmp_path`` tree; the
git calls are recorded at the boundary, so no proof reads the repository's
files to decide whether the mark happened.
"""

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

#: The run and iteration the proofs seal their proposal under.
_RUN_ID = "run-marker"
_ITERATION = 1


class _MarkerRefProbe:
    """Stand in for the git router and watch the marker ref write.

    The probe routes every git call to the real router and records every
    write aimed at the rejected-proposal namespace, so a proof can tell
    "the mark was refused" from "the mark was attempted and git rejected it
    afterwards".

    With an ``answer`` the probe also takes over the marker's own
    ``rev-parse``: it then answers with a value that is NOT a git object
    name. The marker step is the one that runs AFTER the rollback restored
    the tree, so the probe arms itself from the rollback's own step hook.
    """

    def __init__(self, proposal_ref: str, answer: Callable[[], object] | None = None) -> None:
        self._proposal_ref = proposal_ref
        self._answer = answer
        self._marker_phase = False
        self.resolved_sha_calls = 0
        self.marker_ref_writes: list[tuple[str, object]] = []

    def arm_marker_phase(self, step: str) -> None:
        """Arm the probe when the rollback reaches the marker step."""
        if step == "complete":
            self._marker_phase = True

    def __call__(
        self,
        repo: GitMethodRepo,
        method_name: str,
        args: Sequence[object] = (),
        kwargs: Mapping[str, object] | None = None,
    ) -> object:
        """Route one git call, substituting the bad answer at the marker step."""
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
    """Dirty the working tree and seal the proposal.

    Returns:
        The sealed proposal and the path of the file the proposal added.
    """
    tracked = repo_path / "README.md"
    tracked.write_text("modified by the proposal\n", encoding="utf-8")
    added = repo_path / "added.txt"
    added.write_text("added by the proposal\n", encoding="utf-8")
    seal = seal_pending(repo_path, _RUN_ID, _ITERATION)
    return seal, added


def _marker_ref_path(repo_path: Path) -> Path:
    """Return the on-disk path of the rejected-proposal marker ref."""
    marker_ref = f"{REJECTED_PROPOSAL_REF_PREFIX}{_RUN_ID}/{_ITERATION}"
    return repo_path / ".git" / marker_ref


def _warning_sink() -> tuple[list[str], int]:
    """Attach a WARNING-level loguru sink; return its buffer and sink id."""
    records: list[str] = []
    return records, logger.add(records.append, level="WARNING", format="{message}")


def test_marker_ref_is_not_written_when_the_resolved_sha_is_not_an_object_name(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resolved value that is not an object name must not reach any ref.

    The value comes out of an external process, so it is validated before the
    marker ref is written: the rollback stays nonfatal and no ref is pointed
    at the invalid value.
    """
    repo_path = tmp_git_repo
    seal, added = _dirty_proposal(repo_path)

    probe = _MarkerRefProbe(seal.ref, lambda: "not-an-object-name\n")
    monkeypatch.setattr("ralph.git.proposal_seal.route_git_method", probe)

    warnings, sink_id = _warning_sink()
    try:
        rollback(repo_path, seal.ref, step_hook=probe.arm_marker_phase)
    finally:
        logger.remove(sink_id)

    # The marker step really resolved the proposal through the seam, so the
    # proofs below are not vacuous.
    assert probe.resolved_sha_calls == 1
    # No ref was pointed at the invalid value...
    assert probe.marker_ref_writes == []
    # ...and no marker ref was left behind.
    assert not _marker_ref_path(repo_path).exists()
    # The rollback itself is unaffected: it restored the tree cleanly,
    # so the mark stayed a nonfatal failure...
    assert not added.exists()
    # ...and the failure was logged rather than swallowed.
    assert any(seal.ref in message for message in warnings)


def test_marker_ref_is_not_written_when_git_fails_to_resolve_the_proposal(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A git failure while resolving the proposal must stay nonfatal.

    The rollback that already restored the tree must not fail because the
    mark could not be written.
    """

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
    """The mark still happens for a valid resolution.

    This is the control for the two proofs above: the validation must not
    pass by never marking, and the marker must point at the proposal commit.
    """
    repo_path = tmp_git_repo
    seal, added = _dirty_proposal(repo_path)

    probe = _MarkerRefProbe(seal.ref)
    monkeypatch.setattr("ralph.git.proposal_seal.route_git_method", probe)

    rollback(repo_path, seal.ref, step_hook=probe.arm_marker_phase)

    marker_ref = f"{REJECTED_PROPOSAL_REF_PREFIX}{_RUN_ID}/{_ITERATION}"
    assert probe.marker_ref_writes == [(marker_ref, seal.commit_sha)]
    assert _marker_ref_path(repo_path).is_file()
    assert not added.exists()
