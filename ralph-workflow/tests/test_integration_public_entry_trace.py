from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.rebase.rebase import RebaseSuccess
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.integration_actions import (
    AgentRunner,
    GitOps,
    current_agent_runner,
    current_git_ops,
    install_executors,
    reset_executors,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.git.rebase.rebase import RebaseResult
    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

_TARGET = "main"
_FEATURE_SHA = "feature-sha"
_TARGET_SHA = "target-sha"


class _TraceRecorder:
    def __init__(self, *, rebase_result: RebaseResult | None = None) -> None:
        self.mutations: list[str] = []
        self.dispatches: list[str] = []
        self._rebase_result: RebaseResult = (
            rebase_result if rebase_result is not None else RebaseSuccess()
        )
        self.landed = True

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        self.mutations.append("rebase_onto")
        return self._rebase_result

    def abort_rebase(self, root: Path) -> None:
        self.mutations.append("abort_rebase")

    def continue_rebase(self, root: Path) -> None:
        self.mutations.append("continue_rebase")

    def abort_merge(self, root: Path) -> bool:
        self.mutations.append("abort_merge")
        return True

    def continue_merge(self, root: Path) -> bool:
        self.mutations.append("continue_merge")
        return True

    def abort_cherry_pick(self, root: Path) -> bool:
        self.mutations.append("abort_cherry_pick")
        return True

    def continue_cherry_pick(self, root: Path) -> bool:
        self.mutations.append("continue_cherry_pick")
        return True

    def stage_all(self, root: Path) -> bool:
        self.mutations.append("stage_all")
        return True

    def commit(
        self,
        root: Path,
        *,
        on_failure: Callable[[str], None] | None = None,
    ) -> bool:
        self.mutations.append("commit")
        return True

    def fast_forward_target(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        self.mutations.append("fast_forward_target")
        return self.landed, "" if self.landed else "target advanced concurrently"

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
        self.mutations.append("land_feature")
        return self.landed, "" if self.landed else "target advanced concurrently"

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        self.mutations.append("create_backup_ref")
        return "refs/rebase-backup/trace"

    def delete_backup_ref(self, root: Path, backup_ref: str | None) -> None:
        self.mutations.append("delete_backup_ref")

    def reattach_head(self, root: Path, branch: str) -> bool:
        self.mutations.append("reattach_head")
        return True

    def resolve_rebase(
        self,
        root: Path,
        target: str,
        resolver: RebaseStopResolver,
        *,
        session: ResolutionSession,
    ) -> bool:
        self.dispatches.append("resolve_rebase")
        return True


def _forbid_production_mutation(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("a mutation bypassed the injected executor")


def _stub_repository_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.resolve_integration_target",
        lambda *_args, **_kwargs: _TARGET,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._current_branch_or_detached_marker",
        lambda *_args, **_kwargs: "feature",
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._refresh_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.branch_sha",
        lambda *_args, **_kwargs: _TARGET_SHA,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.get_head_sha",
        lambda *_args, **_kwargs: _FEATURE_SHA,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.check_rebase_preconditions",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.observe_conflict_identity",
        lambda *_args, **_kwargs: ConflictIdentity(
            feature_sha=_FEATURE_SHA,
            target_sha=_TARGET_SHA,
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._recover_before_attempt",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.post_attempt_verify",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.contained_land",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record_outcome",
        lambda *_args, **_kwargs: RecordReadOutcome(
            disposition=RecordReadOutcomeDisposition.ABSENT,
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._write_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._clear_record",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._read_post_integration_head_sha",
        lambda *_args, **_kwargs: _FEATURE_SHA,
    )


def _stub_replay_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.run_git",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="unavailable",
        ),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.clear_untracked_merge_obstructions",
        lambda *_args, **_kwargs: None,
    )


def _forbid_bypass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_backup_refs.create_rebase_backup_ref",
        _forbid_production_mutation,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_backup_refs.delete_rebase_backup_ref",
        _forbid_production_mutation,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_ff.fast_forward_target",
        _forbid_production_mutation,
    )
    monkeypatch.setattr(
        "ralph.git.rebase.rebase.rebase_onto",
        _forbid_production_mutation,
    )


def _build_config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_remote_enabled": False,
            },
        },
    )


def test_the_public_entry_routes_every_mutation_through_the_executor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _TraceRecorder()
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    outcome = auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        git_ops=recorder,
        agent_runner=recorder,
    )

    assert outcome is not None
    assert recorder.mutations == [
        "create_backup_ref",
        "rebase_onto",
        "fast_forward_target",
        "delete_backup_ref",
    ]
    assert recorder.dispatches == []
    assert outcome.fast_forwarded is True


def test_the_executor_scope_does_not_leak_past_the_public_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _TraceRecorder()
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        git_ops=recorder,
        agent_runner=recorder,
    )

    assert current_git_ops() is not recorder
    assert current_agent_runner() is not recorder


def test_an_environmental_failure_buys_no_agent_dispatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _TraceRecorder()
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    def _unreadable_repository(root: Path, target: str) -> RebaseResult:
        raise OSError("the repository could not be read")

    monkeypatch.setattr(recorder, "rebase_onto", _unreadable_repository)

    outcome = auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        git_ops=recorder,
        agent_runner=recorder,
    )

    assert outcome is not None
    assert recorder.dispatches == []
    assert "abort_rebase" not in recorder.mutations
    assert "fast_forward_target" not in recorder.mutations


def test_a_landing_that_lost_the_race_keeps_the_backup_ref(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _TraceRecorder()
    recorder.landed = False
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    outcome = auto_integrate_after_commit(
        _build_config(),
        WorkspaceScope(tmp_path),
        RebaseState(),
        git_ops=recorder,
        agent_runner=recorder,
    )

    assert outcome is not None
    assert recorder.mutations == [
        "create_backup_ref",
        "rebase_onto",
        "fast_forward_target",
    ]
    assert outcome.fast_forwarded is False


def test_the_ambient_executor_scope_reaches_the_public_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorder = _TraceRecorder()
    _stub_repository_reads(monkeypatch)
    _stub_replay_probes(monkeypatch)
    _forbid_bypass(monkeypatch)

    token = install_executors(recorder, recorder)
    try:
        auto_integrate_after_commit(
            _build_config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
        )
    finally:
        reset_executors(token)

    assert "rebase_onto" in recorder.mutations


def test_the_injected_double_satisfies_both_executor_protocols() -> None:
    recorder = _TraceRecorder()
    assert isinstance(recorder, GitOps)
    assert isinstance(recorder, AgentRunner)
