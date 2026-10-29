"""The durable state a SUCCESSFUL integration landing carries.

A landed feature is the product's headline success fact, so the state
that survives the attempt must say exactly that: the feature landed, and
nothing is left over from the rebase/merge phase that preceded it.

The success branch of :mod:`ralph.pipeline.auto_integrate` used to keep
the rebase phase's reason on the landing state while its comment claimed
the reason was scrubbed, so a stale rebase-phase reason could reach the
durable state next to ``fast_forwarded=True``. These tests pin the
intended durable state:

* a successful landing carries NO rebase-phase reason;
* the ONE exception is a REGISTERED rebase-skip reason, the only way the
  operator learns that the rebase never ran.

The assertions read the PUBLIC result of the PUBLIC integration seam
(``auto_integrate_after_commit`` -> ``RebaseState``): that object is what
the runner renders and what the run checkpoint persists, so it is the
observable durable state of one landing attempt.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.branch_relation import BranchRelation
from ralph.git.merge import MergeResult
from ralph.git.rebase.rebase import RebaseResult, RebaseSuccess
from ralph.git.rebase.rebase_conflicts import RebaseConflicts
from ralph.git.rebase.rebase_no_op import RebaseNoOp
from ralph.pipeline.auto_integrate import auto_integrate_after_commit
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_rebase_merge import (
    SKIP_RETRY_AFTER_MERGE_COMMIT,
    is_registered_skip_reason,
    skip_reason_slug,
)
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcome,
    RecordReadOutcomeDisposition,
)
from ralph.pipeline.auto_integrate_resolution_state import ContainmentVerdict
from ralph.pipeline.auto_integrate_transaction import LeaseOutcome
from ralph.pipeline.integration_actions import install_executors, reset_executors
from ralph.pipeline.integration_resolution import TreeReadinessAssessment
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from ralph.pipeline._auto_integrate_reclaim import ReclaimResult
    from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStopResolver
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

pytestmark = [pytest.mark.timeout_seconds(10)]

_TARGET = "main"
_TARGET_SHA = "a" * 40
_FEATURE_SHA = "b" * 40

#: The benign reason the rebase engine reports when the branch is already
#: aligned with the target. It is a rebase-PHASE reason: it says what the
#: rebase did, not what the landing did.
_REBASE_PHASE_REASON = "Branch is already up-to-date with upstream"


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


class _TraceRecorder:
    """Git-mutation double that records the order of an attempt."""

    def __init__(self) -> None:
        self.mutations: list[str] = []
        self.rebase_result: RebaseResult = RebaseSuccess()
        self.land_failures = 0

    def rebase_onto(self, root: Path, target: str) -> RebaseResult:
        self.mutations.append("rebase_onto")
        return self.rebase_result

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
        if self.land_failures > 0:
            self.land_failures -= 1
            return False, "target advanced concurrently (not an ancestor of feature)"
        return True, ""

    def land_feature(
        self,
        root: Path,
        target: str,
        feature_sha: str,
        *,
        reclaim_target_worktree: bool = True,
        on_reclaimed: Callable[[ReclaimResult], None] | None = None,
    ) -> tuple[bool, str]:
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
        self.mutations.append("resolve_rebase")
        return True


def _stubbed(name: str, value: object) -> tuple[str, object]:
    return (name, value)


@pytest.fixture(autouse=True)
def _stub_repository_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace every git READ the integration seams perform."""
    for target, value in (
        _stubbed(
            "ralph.pipeline.auto_integrate.resolve_integration_target",
            lambda *_args, **_kwargs: _TARGET,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate._current_branch_or_detached_marker",
            lambda *_args, **_kwargs: "feature",
        ),
        _stubbed("ralph.pipeline.auto_integrate._refresh_target", lambda *_args, **_kwargs: None),
        _stubbed("ralph.pipeline.auto_integrate.branch_sha", lambda *_args, **_kwargs: _TARGET_SHA),
        _stubbed(
            "ralph.pipeline.auto_integrate.get_head_sha", lambda *_args, **_kwargs: _FEATURE_SHA
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate.check_rebase_preconditions",
            lambda *_args, **_kwargs: None,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate.observe_conflict_identity",
            lambda *_args, **_kwargs: ConflictIdentity(
                feature_sha=_FEATURE_SHA,
                target_sha=_TARGET_SHA,
            ),
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate._recover_before_attempt",
            lambda *_args, **_kwargs: None,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate.post_attempt_verify",
            lambda *_args, **_kwargs: None,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate.contained_land",
            lambda *_args, **_kwargs: None,
        ),
        _stubbed("ralph.pipeline.auto_integrate.read_record", lambda *_args, **_kwargs: None),
        _stubbed(
            "ralph.pipeline.auto_integrate.read_record_outcome",
            lambda *_args, **_kwargs: RecordReadOutcome(
                disposition=RecordReadOutcomeDisposition.ABSENT,
            ),
        ),
        _stubbed("ralph.pipeline.auto_integrate._write_record", lambda *_args, **_kwargs: None),
        _stubbed("ralph.pipeline.auto_integrate._clear_record", lambda *_args, **_kwargs: None),
        _stubbed(
            "ralph.pipeline.auto_integrate._read_post_integration_head_sha",
            lambda *_args, **_kwargs: _FEATURE_SHA,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate._reattach_head_if_detached",
            lambda *_args, **_kwargs: None,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_resolution_state.evaluate_integration_containment",
            lambda root, target, **_kwargs: ContainmentVerdict(
                contained=False,
                target=target,
                feature_sha=_FEATURE_SHA,
                target_sha=_TARGET_SHA,
                evidence="uncontained",
            ),
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_rebase_merge.run_git",
            lambda *_args, **_kwargs: SimpleNamespace(
                returncode=1,
                stdout="",
                stderr="unavailable",
            ),
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_rebase_merge.clear_untracked_merge_obstructions",
            lambda *_args, **_kwargs: None,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_rebase_merge.rebase_in_progress",
            lambda *_args, **_kwargs: False,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_planning.resolve_integration_target",
            lambda *_args, **_kwargs: _TARGET,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_planning.branch_relation",
            lambda *_args, **_kwargs: BranchRelation.DIVERGED,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_planning.assess_tree_readiness",
            lambda *_args, **_kwargs: TreeReadinessAssessment(
                development_dispatch_allowed=True,
                integration_ready=True,
            ),
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_planning.retained_integration_reason",
            lambda *_args, **_kwargs: None,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_planning.rebase_in_progress",
            lambda *_args, **_kwargs: False,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_planning._reattach_head_if_detached",
            lambda *_args, **_kwargs: None,
        ),
        _stubbed(
            "ralph.pipeline.auto_integrate_recovery_terminal.post_attempt_verify",
            lambda *_args, **_kwargs: None,
        ),
    ):
        monkeypatch.setattr(target, value)

    @contextmanager
    def _fake_transaction(_root: Path) -> Iterator[LeaseOutcome]:
        yield LeaseOutcome.acquired_outcome()

    for module in (
        "ralph.pipeline.auto_integrate",
        "ralph.pipeline.auto_integrate_transaction",
        "ralph.pipeline.auto_integrate_recovery",
    ):
        monkeypatch.setattr(f"{module}.integration_transaction", _fake_transaction)


def _isolate_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the LANDING contract from the publication contract.

    The publication seam always records its own outcome in
    ``last_reason`` (a converged push, an unreachable remote, or the
    "remote sync disabled by policy" no-op), so a landing state observed
    THROUGH it can never show what the landing itself recorded. This
    stub keeps the landing's own reason visible by leaving the source
    state untouched; the publication reasons keep their own coverage.
    """

    def _no_publication(
        *_args: object,
        source_state: RebaseState | None = None,
        **_kwargs: object,
    ) -> RebaseState:
        return source_state if source_state is not None else RebaseState()

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.reconcile_remote_publication",
        _no_publication,
    )


def test_a_successful_landing_carries_no_rebase_phase_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A landed feature carries no reason from the rebase/merge phase.

    The rebase phase reported a benign no-op reason; the fast-forward
    then landed the feature. The durable state must say "it landed" and
    nothing else: the rebase-phase reason must not survive next to
    ``fast_forwarded=True``.
    """
    recorder = _TraceRecorder()
    recorder.rebase_result = RebaseNoOp(_REBASE_PHASE_REASON)
    _isolate_publication(monkeypatch)
    token = install_executors(recorder, recorder)
    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
            sleep=lambda _delay: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)
    assert outcome is not None
    assert outcome.fast_forwarded is True, (
        f"the feature must have landed, got {outcome.fast_forwarded!r}"
    )
    assert outcome.last_reason is None, (
        "a successful landing must not persist the rebase phase's reason, "
        f"got {outcome.last_reason!r}"
    )
    assert recorder.mutations.count("fast_forward_target") == 1


def test_a_successful_landing_keeps_the_registered_rebase_skip_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The registered rebase-skip reason is the ONE reason a landing keeps.

    A retry straight after a merge commit skips the rebase for a
    registered reason. That reason is how the operator learns the rebase
    never ran, so the success branch must NOT scrub it.
    """
    recorder = _TraceRecorder()
    recorder.rebase_result = RebaseConflicts(files=["shared.txt"])
    recorder.land_failures = 1
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_rebase_merge.endpoint_merge_with_resolution",
        lambda *_args, **_kwargs: MergeResult(outcome="success"),
    )
    _isolate_publication(monkeypatch)
    token = install_executors(recorder, recorder)
    try:
        outcome = auto_integrate_after_commit(
            _config(),
            WorkspaceScope(tmp_path),
            RebaseState(),
            sleep=lambda _delay: None,
            jitter=lambda: 0.0,
        )
    finally:
        reset_executors(token)
    assert outcome is not None
    assert outcome.fast_forwarded is True, (
        f"the feature must have landed, got {outcome.fast_forwarded!r}"
    )
    assert outcome.last_reason is not None
    assert skip_reason_slug(outcome.last_reason) == SKIP_RETRY_AFTER_MERGE_COMMIT
    assert is_registered_skip_reason(outcome.last_reason)
    # The retry took the named skip: the rebase was not attempted twice.
    assert recorder.mutations.count("rebase_onto") == 1
