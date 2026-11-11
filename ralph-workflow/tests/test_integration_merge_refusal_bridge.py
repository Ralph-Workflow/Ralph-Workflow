from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest
from loguru import logger

from ralph.git.git_run_result import GitRunResult
from ralph.git.hardening import COMMIT_PIN_CONFIG_ARGS
from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    MERGE_STATE_NONE,
    MERGE_STATE_UNKNOWN,
    MergeResult,
    merge_refusal_obstruction,
    merge_target_into_current,
)
from ralph.git.rebase.rebase import RebaseConflicts
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.auto_integrate_rebase_merge import run_rebase_or_merge
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import EscalationRung
from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
)
from ralph.pipeline.rebase_state import RebaseOutcomeKind

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from ralph.git.git_call_policy import GitCallClass
    from ralph.git.subprocess_runner import GitRunOptions

pytestmark = pytest.mark.timeout_seconds(10)

_ENGINE_MODULE = "ralph.pipeline.auto_integrate_rebase_merge"

_BLOCK_PATH = "app/models/evidence_artifact.rb"
_BACKUP_REF = "refs/ralph-backup/fake-refusal-01"
_HEAD_SHA = "d" * 40

_RESTORE_ARGV = ("restore", "--worktree", "--staged", "--source=HEAD", "--", _BLOCK_PATH)

_REFUSAL_STDERR = (
    "error: Your local changes to the following files would be overwritten by merge:\n"
    f"\t{_BLOCK_PATH}\n"
    "Please commit your changes or stash them before you merge.\n"
    "Aborting\n"
)


def _typed_refusal() -> MergeResult:
    return MergeResult(
        outcome="conflict",
        reason=f"merge refused: local changes would be overwritten: {_BLOCK_PATH}",
        blocked_paths=(_BLOCK_PATH,),
        failure_cause=ResolutionFailureCause.CONTEXT_FAILURE,
    )


def _clean_observation() -> Observation:
    return Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )


class _StubDirector:
    def __init__(self, observations: list[Observation]) -> None:
        self._observations = list(observations)
        self.observations_made = 0

    def __call__(self, root: Path, **kwargs: object) -> _StubDirector:
        del root, kwargs
        return self

    def observe(self) -> Observation:
        self.observations_made += 1
        if len(self._observations) > 1:
            return self._observations.pop(0)
        return self._observations[0]


class _Recorder:
    def __init__(self, returns: object = None) -> None:
        self.calls: list[tuple[object, ...]] = []
        self._returns = returns

    def __call__(self, *args: object) -> object:
        self.calls.append(args)
        return self._returns


class _GitCalls:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], Path | None]] = []

    def __call__(
        self,
        args: Sequence[str],
        *,
        cwd: Path | None = None,
        label: str = "",
        options: GitRunOptions | None = None,
        call_class: GitCallClass | None = None,
    ) -> GitRunResult:
        del label, options, call_class
        raw = tuple(args)
        self.calls.append((raw, cwd))
        return GitRunResult(args=raw, returncode=0, stdout="", stderr="")


def _restore_invocations(
    calls: list[tuple[tuple[str, ...], Path | None]],
) -> list[tuple[tuple[str, ...], Path | None]]:
    return [
        (argv[len(COMMIT_PIN_CONFIG_ARGS) :], cwd)
        for argv, cwd in calls
        if (
            argv[: len(COMMIT_PIN_CONFIG_ARGS)] == COMMIT_PIN_CONFIG_ARGS
            and argv[len(COMMIT_PIN_CONFIG_ARGS) :][:1] == ("restore",)
        )
    ]


def _install_bridge_seams(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    observations: list[Observation],
    merge_state: str = MERGE_STATE_NONE,
    preserve_returns: tuple[str, str] | Exception = (_BACKUP_REF, _HEAD_SHA),
    refusal: MergeResult | None = None,
) -> tuple[_StubDirector, _Recorder, _GitCalls, list[str], _Recorder]:
    import ralph.pipeline.auto_integrate_rebase_merge as engine
    import ralph.pipeline.integration_director as director_mod

    director = _StubDirector(observations)
    monkeypatch.setattr(director_mod, "IntegrationDirector", director)

    def _fake_merge_state(_root: Path) -> str:
        return merge_state

    def _fake_routing(_root: Path, _target: str) -> None:
        return None

    def _fake_clear(_root: Path, _target: str) -> tuple[str, ...]:
        return ()

    def _fake_rebase(_target: str, repo_root: Path) -> RebaseConflicts:
        del repo_root
        return RebaseConflicts(files=[_BLOCK_PATH])

    def _fake_rebase_in_progress(_root: Path) -> bool:
        return False

    def _fake_verify_terminal(
        _root: Path, *, expected_head_sha: str | None, owns_resolution: bool
    ) -> None:
        del expected_head_sha, owns_resolution

    def _fake_head_sha(_root: Path, _ref: str) -> tuple[str, bool]:
        return _HEAD_SHA, True

    monkeypatch.setattr(engine, "range_routing_reason", _fake_routing)
    monkeypatch.setattr(engine, "clear_untracked_merge_obstructions", _fake_clear)
    monkeypatch.setattr(engine, "rebase_onto", _fake_rebase)
    monkeypatch.setattr(engine, "rebase_in_progress", _fake_rebase_in_progress)
    monkeypatch.setattr(engine, "verify_terminal_state", _fake_verify_terminal)
    monkeypatch.setattr(engine, "merge_state", _fake_merge_state)
    monkeypatch.setattr(engine, "observe_branch_sha", _fake_head_sha)

    preserve = _Recorder(preserve_returns)
    monkeypatch.setattr(engine, "create_stale_resolution_snapshot", preserve)

    git = _GitCalls()
    monkeypatch.setattr(engine, "run_git", git)

    merge = _Recorder(refusal if refusal is not None else _typed_refusal())
    monkeypatch.setattr(engine, "endpoint_merge_with_resolution", merge)

    captured, _sink_id = _capture_warnings()
    _ACTIVE_SINKS.append(_sink_id)
    return director, preserve, git, captured, merge


_ACTIVE_SINKS: list[int] = []


def _drop_sink(_captured: list[str]) -> None:
    while _ACTIVE_SINKS:
        logger.remove(_ACTIVE_SINKS.pop())


class _WarningMessage(Protocol):
    def __str__(self) -> str: ...

    @property
    def record(self) -> Mapping[str, object]: ...


def _capture_warnings() -> tuple[list[str], int]:
    captured: list[str] = []

    def _sink(message: _WarningMessage) -> None:
        if message.record.get("name") == _ENGINE_MODULE:
            captured.append(str(message))

    sink_id = logger.add(_sink, level="WARNING", format="{message}")
    return captured, sink_id


def test_refusal_result_is_a_typed_path_bearing_obstruction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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

        def _result(returncode: int, stdout: str, stderr: str) -> GitRunResult:
            return GitRunResult(args=raw, returncode=returncode, stdout=stdout, stderr=stderr)

        if argv[:2] == ("ls-files", "--others"):
            return _result(0, "", "")
        if argv[0] == "merge" and argv[1] == "--no-edit":
            return _result(2, "", _REFUSAL_STDERR)
        if argv[0] == "rev-parse" and "--git-dir" in argv:
            return _result(0, ".git", "")
        return _result(0, "", "")

    monkeypatch.setattr("ralph.git.merge.run_git", _fake_run_git)
    monkeypatch.setattr("ralph.git.merge_obstructions.run_git", _fake_run_git)

    result = merge_target_into_current(tmp_path, "main")

    assert result.outcome == "conflict"
    assert result.blocked_paths == (_BLOCK_PATH,)
    assert result.failure_cause is ResolutionFailureCause.CONTEXT_FAILURE
    assert merge_refusal_obstruction(result) == (_BLOCK_PATH,)


def test_merge_refusal_obstruction_projection_is_closed() -> None:
    assert (
        merge_refusal_obstruction(
            MergeResult(
                outcome="conflict", failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT
            )
        )
        is None
    )
    legacy = MergeResult(
        outcome="conflict",
        reason=f"merge refused: local changes would be overwritten: {_BLOCK_PATH}",
        blocked_paths=(_BLOCK_PATH,),
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )
    assert merge_refusal_obstruction(legacy) is None
    assert merge_refusal_obstruction(MergeResult(outcome="success")) is None
    assert merge_refusal_obstruction(MergeResult(outcome="noop")) is None


def test_clean_observation_overwrite_refusal_is_fully_bridged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    director, preserve, git, warnings, _merge = _install_bridge_seams(
        monkeypatch, tmp_path, observations=[_clean_observation()]
    )
    try:
        resolver_calls: list[object] = []

        def sentinel_resolver(root: Path, target: str) -> bool:
            resolver_calls.append((root, target))
            return True

        result = run_rebase_or_merge(tmp_path, "main", sentinel_resolver)

        state = result.short_circuit
        assert state is not None
        assert state.last_outcome is RebaseOutcomeKind.SKIPPED
        assert not state.integration_unresolved
        assert result.skip_reason is None
        assert state.last_failure_cause is ResolutionFailureCause.CONTEXT_FAILURE
        assert "worktree=tracked_changes" in (state.last_reason or "")
        assert "retrying rebase-first" in (state.last_reason or "")
        assert len(preserve.calls) == 1
        assert preserve.calls[0][0] == tmp_path
        assert (_BACKUP_REF) in (state.last_reason or "")
        restores = _restore_invocations(git.calls)
        assert len(restores) == 1
        assert restores[0] == (_RESTORE_ARGV, tmp_path)
        assert resolver_calls == []
        assert len(warnings) == 1
        assert _BLOCK_PATH in warnings[0]
        assert _BACKUP_REF in warnings[0]
        durable = AttemptLedger(tmp_path).read()
        assert sum(durable.repeat_counts.values()) == 1
        assert director.observations_made == 1
    finally:
        _drop_sink(warnings)


def test_identical_repeats_spend_budget_and_advance_cycle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _director, preserve, git, warnings, _merge = _install_bridge_seams(
        monkeypatch, tmp_path, observations=[_clean_observation()]
    )
    try:
        for _ in range(8):
            result = run_rebase_or_merge(tmp_path, "main", None)
            state = result.short_circuit
            assert state is not None
            assert state.last_outcome is RebaseOutcomeKind.SKIPPED

        durable = AttemptLedger(tmp_path).read()
        assert sum(durable.repeat_counts.values()) == 8
        assert durable.rung is not EscalationRung.REPAIR
        charged_actions = {action for (_sig, action) in durable.repeat_counts}
        assert "dispatch_resolver" not in charged_actions
        assert "commit" not in charged_actions
        assert len(preserve.calls) == 2
        assert len(_restore_invocations(git.calls)) == 2
        assert len(warnings) == 8
    finally:
        _drop_sink(warnings)


def test_durable_record_is_rewritten_from_the_reobserved_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    staged = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.STAGED,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )
    director, _preserve, _git, warnings, _merge = _install_bridge_seams(
        monkeypatch, tmp_path, observations=[_clean_observation(), staged]
    )
    try:
        first = run_rebase_or_merge(tmp_path, "main", None).short_circuit
        second = run_rebase_or_merge(tmp_path, "main", None).short_circuit
        assert first is not None and second is not None
        assert "worktree=tracked_changes" in (first.last_reason or "")
        assert "worktree=staged" in (second.last_reason or "")
        assert first.last_reason != second.last_reason
        assert director.observations_made == 2
        assert sum(AttemptLedger(tmp_path).read().repeat_counts.values()) == 2
    finally:
        _drop_sink(warnings)


def test_genuine_merge_head_conflict_keeps_existing_contracts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _director, preserve, git, warnings, _merge = _install_bridge_seams(
        monkeypatch,
        tmp_path,
        observations=[_clean_observation()],
        merge_state=MERGE_STATE_IN_PROGRESS,
    )
    try:
        result = run_rebase_or_merge(tmp_path, "main", None)
        state = result.short_circuit
        assert state is not None
        assert state.last_outcome is RebaseOutcomeKind.CONFLICT
        assert preserve.calls == []
        assert _restore_invocations(git.calls) == []
        assert sum(AttemptLedger(tmp_path).read().repeat_counts.values()) == 0
        assert warnings == []
    finally:
        _drop_sink(warnings)


def test_unreadable_probes_back_off_without_acting(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _director, preserve, git, warnings, _merge = _install_bridge_seams(
        monkeypatch,
        tmp_path,
        observations=[_clean_observation()],
        merge_state=MERGE_STATE_UNKNOWN,
    )
    try:
        result = run_rebase_or_merge(tmp_path, "main", None)
        state = result.short_circuit
        assert state is not None
        assert state.last_outcome is RebaseOutcomeKind.CONFLICT
        assert preserve.calls == []
        assert _restore_invocations(git.calls) == []
        assert sum(AttemptLedger(tmp_path).read().repeat_counts.values()) == 0
        assert warnings == []
    finally:
        _drop_sink(warnings)

    unknown_observation = Observation(
        target_vs_feature=TargetVsFeature.BEHIND,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.UNKNOWN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )
    _director2, preserve2, git2, warnings2, _merge2 = _install_bridge_seams(
        monkeypatch, tmp_path, observations=[unknown_observation]
    )
    try:
        result2 = run_rebase_or_merge(tmp_path, "main", None)
        state2 = result2.short_circuit
        assert state2 is not None
        assert state2.last_outcome is RebaseOutcomeKind.CONFLICT
        assert preserve2.calls == []
        assert _restore_invocations(git2.calls) == []
        assert sum(AttemptLedger(tmp_path).read().repeat_counts.values()) == 0
        assert warnings2 == []
    finally:
        _drop_sink(warnings2)


def test_legacy_untyped_refusal_flows_through_safe_reobservation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    legacy = MergeResult(
        outcome="conflict",
        reason=f"merge refused: local changes would be overwritten: {_BLOCK_PATH}",
        blocked_paths=(_BLOCK_PATH,),
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )
    _director, _preserve, _git, warnings, _merge = _install_bridge_seams(
        monkeypatch,
        tmp_path,
        observations=[_clean_observation()],
        refusal=legacy,
    )
    try:
        result = run_rebase_or_merge(tmp_path, "main", None)
        state = result.short_circuit
        assert state is not None
        assert state.last_outcome is RebaseOutcomeKind.SKIPPED
        assert len(_preserve.calls) == 1
        assert len(warnings) == 1
    finally:
        _drop_sink(warnings)

    legacy_in_progress = MergeResult(
        outcome="conflict",
        reason=f"merge refused: local changes would be overwritten: {_BLOCK_PATH}",
        blocked_paths=(_BLOCK_PATH,),
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )
    _d2, preserve2, git2, warnings2, _m2 = _install_bridge_seams(
        monkeypatch,
        tmp_path,
        observations=[_clean_observation()],
        merge_state=MERGE_STATE_IN_PROGRESS,
        refusal=legacy_in_progress,
    )
    try:
        result2 = run_rebase_or_merge(tmp_path, "main", None)
        state2 = result2.short_circuit
        assert state2 is not None
        assert state2.last_outcome is RebaseOutcomeKind.CONFLICT
        assert preserve2.calls == []
        assert _restore_invocations(git2.calls) == []
        assert warnings2 == []
    finally:
        _drop_sink(warnings2)


def test_preservation_failure_is_reported_not_retried_silently(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _director, _preserve, git, warnings, _merge = _install_bridge_seams(
        monkeypatch,
        tmp_path,
        observations=[_clean_observation()],
        preserve_returns=RuntimeError("snapshot failed"),
    )
    try:
        result = run_rebase_or_merge(tmp_path, "main", None)
        state = result.short_circuit
        assert state is not None
        assert state.last_outcome is RebaseOutcomeKind.SKIPPED
        assert "preservation failed" in (state.last_reason or "")
        assert _restore_invocations(git.calls) == []
        assert sum(AttemptLedger(tmp_path).read().repeat_counts.values()) == 1
        assert len(warnings) == 1
    finally:
        _drop_sink(warnings)
