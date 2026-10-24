"""Rebase-first order: replay onto mainline is always attempted first.

Validates:
1. Rebase-first order across all public integration entry paths:
   - auto_integrate_after_commit
   - auto_integrate_on_phase_transition
   - recover_incomplete_integration
2. `rebase_onto` is always executed before `resolve_rebase` or endpoint merge.
3. Permitted skips (merge commits in range, root commits, all-empty replay)
   each name their reason from current observation.
4. Environmental failures (unreadable git, timeout) never dispatch the
   resolver agent or fall back to endpoint merge.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import MERGE_STATE_NONE
from ralph.git.rebase._rebase_kind import RebaseKind
from ralph.git.rebase.rebase import RebaseFailed, RebaseResult, RebaseSuccess
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.git.rebase.rebase_kinds import RebaseErrorKind
from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline.auto_integrate import (
    auto_integrate_after_commit,
    auto_integrate_on_phase_transition,
)
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_rebase_merge import (
    _REASON_ALL_EMPTY,
    _REASON_MERGE_COMMITS,
    _REASON_ROOT_COMMITS,
    RebaseRunResult,
    record_conflict,
)
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.auto_integrate_recovery import recover_incomplete_integration
from ralph.pipeline.auto_integrate_resolution_state import ContainmentVerdict
from ralph.pipeline.integration_actions import (
    install_executors,
    reset_executors,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

pytestmark = [pytest.mark.timeout_seconds(5)]

_TARGET = "main"
_FEATURE_SHA = "feature-sha-1234"
_TARGET_SHA = "target-sha-5678"


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
        return self.fast_forward_target(
            root,
            target,
            feature_sha,
            reclaim_target_worktree=reclaim_target_worktree,
            on_reclaimed=on_reclaimed,
        )

    def create_backup_ref(
        self,
        root: Path,
        pre_feature_sha: str | None,
        owning_worktree: str | None = None,
    ) -> str | None:
        self.mutations.append("create_backup_ref")
        return "refs/ralph-backup/feature"

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


@pytest.fixture(autouse=True)
def _stub_repo_reads(monkeypatch: pytest.MonkeyPatch) -> None:
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
        "ralph.git.operations.get_head_sha",
        lambda *_args, **_kwargs: _FEATURE_SHA,
    )
    monkeypatch.setattr(
        "ralph.pipeline._owned_landing_reintegration.get_head_sha",
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
    _record_store: dict[str, object] = {}

    def _save_record(_root: Path, rec: object) -> None:
        _record_store["current"] = rec

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._write_record",
        _save_record,
    )
    monkeypatch.setattr(
        "ralph.pipeline._owned_landing_reintegration.write_record",
        _save_record,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.read_record",
        lambda *_args, **_kwargs: _record_store.get("current"),
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
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.rebase_in_progress",
        lambda *_args, **_kwargs: False,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate._reattach_head_if_detached",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_resolution_state.evaluate_integration_containment",
        lambda root, target, **_kwargs: ContainmentVerdict(
            contained=False,
            target=target,
            feature_sha=_FEATURE_SHA,
            target_sha=_TARGET_SHA,
            evidence="uncontained",
        ),
    )

    @contextmanager
    def _fake_tx(_root: Path) -> Iterator[bool]:
        yield True

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.integration_transaction",
        _fake_tx,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_transaction.integration_transaction",
        _fake_tx,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_recovery.integration_transaction",
        _fake_tx,
    )
    monkeypatch.setattr(
        "ralph.git.merge.merge_state",
        lambda _root: MERGE_STATE_NONE,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_recovery.merge_state",
        lambda _root: MERGE_STATE_NONE,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_recovery_record._retained_resolution_is_stale",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        "ralph.git.rebase.rebase_continuation.rebase_in_progress_at",
        lambda _root: False,
    )
    monkeypatch.setattr(
        "ralph.pipeline._owned_landing_reintegration.rebase_in_progress_at",
        lambda _root: False,
    )
    monkeypatch.setattr(
        "ralph.pipeline._owned_landing_reintegration.is_repo_clean",
        lambda _root: True,
    )


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": _TARGET,
                "auto_integrate_remote_enabled": False,
            }
        }
    )


def test_auto_integrate_after_commit_tries_rebase_first(tmp_path: Path) -> None:
    """Entry 1: auto_integrate_after_commit runs rebase_onto before landing."""
    recorder = _TraceRecorder()
    token = install_executors(recorder, recorder)
    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
        )
        assert outcome is not None
        assert "rebase_onto" in recorder.mutations
        assert "fast_forward_target" in recorder.mutations
        rebase_idx = recorder.mutations.index("rebase_onto")
        ff_idx = recorder.mutations.index("fast_forward_target")
        assert rebase_idx < ff_idx
        # No agent needed on clean rebase
        assert recorder.dispatches == []
    finally:
        reset_executors(token)


def test_auto_integrate_after_commit_conflict_dispatches_resolver_after_rebase(
    tmp_path: Path,
) -> None:
    """Conflict during rebase_onto dispatches resolve_rebase only AFTER rebase_onto."""
    recorder = _TraceRecorder(rebase_result=RebaseConflicts(files=["conflicted.txt"]))
    token = install_executors(recorder, recorder)
    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
            rebase_stop_resolver=lambda *_a, **_kw: True,
        )
        assert outcome is not None
        assert recorder.mutations[0] == "create_backup_ref"
        assert recorder.mutations[1] == "rebase_onto"
        assert recorder.dispatches == ["resolve_rebase"]
    finally:
        reset_executors(token)


def test_auto_integrate_on_phase_transition_tries_rebase_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry 2: auto_integrate_on_phase_transition runs rebase_onto first."""
    recorder = _TraceRecorder()
    token = install_executors(recorder, recorder)
    try:
        (tmp_path / ".git").mkdir()
        monkeypatch.setattr(
            "ralph.pipeline.auto_integrate._phase_boundary_outcome",
            lambda *_args, **_kwargs: (False, None),
        )
        outcome = auto_integrate_on_phase_transition(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
        )
        assert outcome is not None
        assert "rebase_onto" in recorder.mutations, f"outcome={outcome!r}"
    finally:
        reset_executors(token)


def test_recover_incomplete_integration_tries_rebase_first(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entry 3: recover_incomplete_integration on integrating record retries rebase."""
    recorder = _TraceRecorder()
    token = install_executors(recorder, recorder)
    try:
        record = IntegrationRecord(
            phase="integrating",
            target=_TARGET,
            pre_feature_sha=_FEATURE_SHA,
            pre_target_sha=_TARGET_SHA,
        )
        monkeypatch.setattr(
            "ralph.pipeline.auto_integrate_recovery.read_record_outcome",
            lambda *_args, **_kwargs: RecordReadOutcome(
                disposition=RecordReadOutcomeDisposition.PRESENT,
                record=record,
            ),
        )
        monkeypatch.setattr(
            "ralph.pipeline.auto_integrate_recovery._read_record",
            lambda *_args, **_kwargs: record,
        )
        monkeypatch.setattr(
            "ralph.pipeline.auto_integrate_recovery.decide_integration",
            lambda *_args, **_kwargs: (
                IntegrationAction.REINTEGRATE_AGAINST_TARGET,
                "reintegrate",
            ),
        )
        outcome = recover_incomplete_integration(
            WorkspaceScope(tmp_path),
            config=_config(),
            rebase_stop_resolver=lambda *_a, **_kw: True,
        )
        assert outcome is not None
        # Recovery through the owner ladder invokes rebase_onto
        assert "rebase_onto" in recorder.mutations, f"outcome={outcome!r}"
    finally:
        reset_executors(token)


@pytest.mark.parametrize(
    ("extra_args_match", "expected_reason"),
    [
        (("--merges",), _REASON_MERGE_COMMITS),
        (("--max-parents=0",), _REASON_ROOT_COMMITS),
        (("--cherry-pick", "--no-merges"), _REASON_ALL_EMPTY),
    ],
)
def test_permitted_skips_name_reason_from_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra_args_match: tuple[str, ...],
    expected_reason: str,
) -> None:
    """Permitted skips (merges, roots, all-empty) bypass rebase_onto with structured reason."""
    recorder = _TraceRecorder()
    token = install_executors(recorder, recorder)

    def _mock_rev_list_count(
        _root: Path,
        _target: str,
        extra_args: tuple[str, ...] = (),
    ) -> int | None:
        if extra_args == ():
            return 3  # total commits > 0
        if extra_args == extra_args_match:
            # For all-empty replay, cherry_count == 0 signals all-empty
            if extra_args == ("--cherry-pick", "--no-merges"):
                return 0
            return 1  # positive match for merges or roots
        if extra_args == ("--cherry-pick", "--no-merges"):
            return 3  # not all empty
        return 0

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge._rev_list_count",
        _mock_rev_list_count,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge._endpoint_merge_result",
        lambda root, target, rebase_outcome, resolver: RebaseRunResult(
            rebase_outcome=rebase_outcome,
            merge_attempted=True,
            merge_outcome=None,
            short_circuit=record_conflict(
                reason=rebase_outcome.reason if hasattr(rebase_outcome, "reason") else "",
                target=target,
            ),
        ),
    )

    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
        )
        assert outcome is not None
        assert expected_reason in (outcome.last_reason or "")
        # Rebase was skipped as permitted
        assert "rebase_onto" not in recorder.mutations
    finally:
        reset_executors(token)


def test_environmental_failure_never_dispatches_agent_or_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Environmental failure (timeout, I/O error) never dispatches agent or falls back to merge."""
    recorder = _TraceRecorder(
        rebase_result=RebaseFailed(kind=RebaseErrorKind(kind=RebaseKind.ENVIRONMENT_FAILURE))
    )
    token = install_executors(recorder, recorder)

    # If endpoint merge were mistakenly called, fail immediately
    def _forbid_merge(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("endpoint merge was called on an environmental failure")

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.endpoint_merge_with_resolution",
        _forbid_merge,
    )

    try:
        auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
            rebase_stop_resolver=lambda *_a, **_kw: True,
        )

        assert "rebase_onto" in recorder.mutations
        assert recorder.dispatches == []
    finally:
        reset_executors(token)
