"""Merge obstructions must never silently strand a branch behind its target.

Observed 2026-10-05: a worktree held 22 untracked files that were
byte-identical to files ``main`` tracks (leftovers of an earlier
integration). ``git merge main`` refused to start (exit 2, "untracked
working tree files would be overwritten by merge"); Ralph recorded a
generic "endpoint merge conflicted" with no resolver able to act, saw a
clean tree, and began planning 40 commits behind ``main``.

Git is injected through ``run_git`` in ``ralph.git.merge`` and
``ralph.git.merge_obstructions`` so this file stays in
the default, budget-tracked suite; only ``tmp_path`` files are touched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.git_run_result import GitRunResult
from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import MergeResult, merge_target_into_current
from ralph.git.merge_obstructions import ancestry_state, clear_untracked_merge_obstructions
from ralph.pipeline.auto_integrate_outcome import classify_merge_only_outcome

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    import pytest

    from ralph.git.git_call_policy import GitCallClass
    from ralph.git.subprocess_runner import GitRunOptions

_SAME_OID = "a" * 40
_TARGET_OID_FOR_EDITED = "b" * 40
_LOCAL_OID_FOR_EDITED = "c" * 40

_UNTRACKED_REFUSAL = (
    "error: The following untracked working tree files would be overwritten by merge:\n"
    "\tdocs/edited.md\n"
    "Please move or remove them before you merge.\n"
    "Aborting\n"
)


def _stub_git(
    monkeypatch: pytest.MonkeyPatch,
    respond: Callable[[tuple[str, ...]], tuple[int, str, str]],
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def _fake_run_git(
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        label: str = "",
        options: GitRunOptions | None = None,
        call_class: GitCallClass | None = None,
    ) -> GitRunResult:
        del cwd, label, options, call_class
        raw = tuple(args)
        argv = (
            raw[len(COMMIT_PIN_CONFIG_ARGS) :]
            if raw[: len(COMMIT_PIN_CONFIG_ARGS)] == COMMIT_PIN_CONFIG_ARGS
            else raw
        )
        calls.append(argv)
        returncode, stdout, stderr = respond(argv)
        return GitRunResult(args=raw, returncode=returncode, stdout=stdout, stderr=stderr)

    monkeypatch.setattr("ralph.git.merge.run_git", _fake_run_git)
    monkeypatch.setattr("ralph.git.merge_obstructions.run_git", _fake_run_git)
    return calls


def _obstruction_repo(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "same.md").write_text("same\n", encoding="utf-8")
    (tmp_path / "docs" / "edited.md").write_text("local work\n", encoding="utf-8")
    (tmp_path / "scratch.txt").write_text("not in target\n", encoding="utf-8")


def _obstruction_git(argv: tuple[str, ...]) -> tuple[int, str, str]:
    if argv[:2] == ("ls-files", "--others"):
        return 0, "docs/same.md\0docs/edited.md\0scratch.txt\0", ""
    if argv[:2] == ("ls-tree", "-r"):
        return (
            0,
            f"100644 blob {_SAME_OID}\tdocs/same.md\0"
            f"100644 blob {_TARGET_OID_FOR_EDITED}\tdocs/edited.md\0",
            "",
        )
    if argv[0] == "hash-object":
        oids = {"docs/edited.md": _LOCAL_OID_FOR_EDITED, "docs/same.md": _SAME_OID}
        return 0, "".join(f"{oids[path]}\n" for path in argv[2:]), ""
    return 0, "", ""


def test_identical_untracked_obstruction_is_removed_and_local_work_is_kept(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _obstruction_repo(tmp_path)
    _stub_git(monkeypatch, _obstruction_git)

    removed = clear_untracked_merge_obstructions(tmp_path, "main")

    assert removed == ("docs/same.md",)
    assert not (tmp_path / "docs" / "same.md").exists()
    assert (tmp_path / "docs" / "edited.md").read_text(encoding="utf-8") == "local work\n"
    assert (tmp_path / "scratch.txt").exists()


def test_unreadable_target_tree_removes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _obstruction_repo(tmp_path)

    def _respond(argv: tuple[str, ...]) -> tuple[int, str, str]:
        if argv[:2] == ("ls-tree", "-r"):
            return 128, "", "fatal: not a valid object name main"
        return _obstruction_git(argv)

    _stub_git(monkeypatch, _respond)

    assert clear_untracked_merge_obstructions(tmp_path, "main") == ()
    assert (tmp_path / "docs" / "same.md").exists()


def test_merge_clears_identical_obstructions_before_merging(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _obstruction_repo(tmp_path)
    calls = _stub_git(monkeypatch, _obstruction_git)

    assert merge_target_into_current(tmp_path, "main") == MergeResult(outcome="success")
    merge_index = calls.index(("merge", "--no-edit", "--", "main"))
    assert any(call[0] == "hash-object" for call in calls[:merge_index])
    assert not (tmp_path / "docs" / "same.md").exists()


def test_refused_merge_names_the_obstruction_instead_of_a_generic_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _obstruction_repo(tmp_path)

    def _respond(argv: tuple[str, ...]) -> tuple[int, str, str]:
        if argv[0] == "merge" and argv[1] == "--no-edit":
            return 2, "", _UNTRACKED_REFUSAL
        return _obstruction_git(argv)

    _stub_git(monkeypatch, _respond)

    result = merge_target_into_current(tmp_path, "main")

    assert result.outcome == "conflict"
    assert result.reason == ("merge refused: untracked files would be overwritten: docs/edited.md")
    assert result.blocked_paths == ("docs/edited.md",)
    _action, headline = classify_merge_only_outcome(result)
    assert headline == (
        "endpoint merge refused: untracked files would be overwritten: docs/edited.md"
    )


def test_repeat_identical_refusal_spends_durable_budget_and_climbs(
    tmp_path: Path,
) -> None:
    """AC-11(d): the refusal evidence is charged durably once per attempt.

    The 2026-10-27 loop logged ``counted=False ... remaining=None`` and a
    fixed delay: the refusal spent nothing, so it could never exhaust
    anything. Here the refusal's typed evidence (the MergeResult the
    boundary receives, including ``blocked_paths``) is folded into the
    observation the owner charges, so a repeat of the SAME refusal
    evidence:

    * advances the durable repeat count once per attempt (``remaining``
      decreases attempt by attempt), and
    * advances the escalation ``cycle`` (the rung) at the threshold,
      reaching a named escalation state within a fixed bound.
    """
    from ralph.pipeline.integration_attempt_ledger import AttemptLedger
    from ralph.pipeline.integration_decision import (
        AttemptState,
        DecisionContext,
        DirtAttribution,
        EscalationRung,
        NextAction,
        decide,
    )
    from ralph.pipeline.integration_observation import (
        DirtShape,
        HeadState,
        InProgressKind,
        InProgressOwnership,
        IntegrationRecordState,
        Observation,
        TargetVsFeature,
        TargetVsRemote,
        WorktreeState,
    )

    # The refusal result from test_refused_merge_names_the_obstruction_...
    # folded into the observation the boundary decides on: the live
    # observation says clean, but the refusal's own file list proves the
    # tree holds local work Git counts, so the observation the budget is
    # charged against carries the blocking worktree state.
    refusal = MergeResult(
        outcome="conflict",
        reason=(
            "merge refused: local changes would be overwritten: app/models/evidence_artifact.rb"
        ),
        blocked_paths=("app/models/evidence_artifact.rb",),
    )
    assert refusal.blocked_paths, "the refusal must carry its file list"

    observation = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.TRACKED_CHANGES,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )

    def _decide(attempts: AttemptState) -> NextAction:
        return decide(
            DecisionContext(
                observation=observation,
                attempts=attempts,
                dirt_shape=DirtShape.UNSTAGED_MODIFICATION,
                dirt_attribution=DirtAttribution.UNOWNED,
            )
        )

    ledger = AttemptLedger(tmp_path, ledger_path=tmp_path / ".agent" / "refusal-ledger.json")
    signature = AttemptState().signature(observation)
    trace: list[NextAction] = []
    for _ in range(6):
        state = ledger.read()
        attempts = AttemptState(
            rung=state.rung,
            repeat_counts=state.repeat_counts,
            unknown_attempts=state.unknown_attempts,
            unknown_budget=state.unknown_budget,
            repeat_threshold=state.repeat_threshold,
        )
        action = _decide(attempts)
        trace.append(action)
        ledger.charge(signature, action.value, is_unknown=False)

    # ``remaining`` advances: every attempt charged the same evidence
    # once — the signature-level total equals the number of attempts,
    # so no retry ever ran for free (the observed run's ``counted=False
    # ... remaining=None``).
    durable = ledger.read()
    assert sum(durable.repeat_counts.values()) == len(trace), (
        "each repeat of the identical refusal evidence charges the durable budget once"
    )
    assert durable.repeat_counts[(signature, trace[0].value)] == 2, (
        "the direct action is charged exactly at the repeat threshold, never re-run past it"
    )
    # The first contact answers the direct cleanup action twice; the
    # escalation cycle advances at the threshold and never re-runs the
    # action that failed — within a fixed bound (here, by the third
    # attempt) the trace reaches the named stronger rung.
    assert trace[0] is NextAction.SAVE_AND_RESTORE
    assert trace[1] is NextAction.SAVE_AND_RESTORE
    assert trace[2] is NextAction.PRESERVE_VIA_TEMP_INDEX
    assert any(action is not NextAction.SAVE_AND_RESTORE for action in trace[2:])
    assert durable.rung is not EscalationRung.REPAIR
    # No resolver and no commit agent for a non-conflict refusal.
    assert NextAction.DISPATCH_RESOLVER not in trace
    assert NextAction.COMMIT not in trace


def test_ancestry_state_separates_no_from_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    codes = iter((0, 1, 128))
    _stub_git(monkeypatch, lambda _argv: (next(codes), "", ""))

    assert ancestry_state("/repo", "main", "HEAD") is True
    assert ancestry_state("/repo", "main", "HEAD") is False
    assert ancestry_state("/repo", "main", "HEAD") is None
