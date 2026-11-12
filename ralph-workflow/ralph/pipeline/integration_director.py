from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from ralph.git.merge import observe_branch_sha
from ralph.git.operations import get_head_sha
from ralph.pipeline._git_operation_authority import GitOperationKind, classify_git_operation
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcomeDisposition,
    clear_record,
    read_record_outcome,
    record_path,
)
from ralph.pipeline.integration_decision import (
    DecisionContext,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
    classify_worktree,
    read_head_state,
    read_in_progress_kind,
    read_merge_base,
    read_worktree_porcelain,
    reading_from_callable,
)
from ralph.pipeline.integration_owner import (
    charge_integration_attempt,
    load_integration_attempts,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_attempt_ledger import AttemptLedgerState


DEFAULT_TARGET_BRANCH: Final[str] = "main"

DEFAULT_REMOTE: Final[str] = "origin"

_PROBE_REASON_MAX_LEN: Final[int] = 120

_UNSUPPORTED_SCHEMA_REASON: Final[str] = "unsupported_schema"

__all__ = [
    "DEFAULT_REMOTE",
    "DEFAULT_TARGET_BRANCH",
    "DirectorDecision",
    "IntegrationDirector",
    "observe",
]


@dataclass(frozen=True)
class DirectorDecision:
    action: NextAction
    observation: Observation
    attempts: AttemptLedgerState
    release_applied: bool


def _read_porcelain(root: Path) -> Reading[str]:
    return read_worktree_porcelain(root)


def _read_branch_sha(root: Path, ref: str) -> tuple[str | None, bool]:
    try:
        return observe_branch_sha(root, ref)
    except Exception:
        return None, False


def _read_head_sha(root: Path) -> Reading[str]:
    return reading_from_callable(lambda: get_head_sha(root))


def _read_head_state(
    root: Path,
    head_probe: Callable[[Path], Reading[HeadState]] | None,
) -> Reading[HeadState]:
    probe = head_probe if head_probe is not None else read_head_state
    try:
        result = probe(root)
    except Exception as exc:
        return Reading.unknown(f"head probe failed: {exc or type(exc).__name__}")
    if not result.is_known_good:
        return Reading.unknown(result.reason or "head probe did not answer")
    value = result.value
    if value not in (HeadState.ATTACHED, HeadState.DETACHED):
        return Reading.unknown("head probe returned an invalid state")
    return Reading.known(value)


def _read_in_progress_kind(
    root: Path,
    in_progress_probe: Callable[[Path], Reading[InProgressKind]] | None,
) -> Reading[InProgressKind]:
    probe = in_progress_probe if in_progress_probe is not None else read_in_progress_kind
    try:
        result = probe(root)
    except Exception as exc:
        return Reading.unknown(f"in-progress probe failed: {exc or type(exc).__name__}")
    if not result.is_known_good:
        return Reading.unknown(result.reason or "in-progress probe did not answer")
    value = result.value
    if value not in tuple(InProgressKind):
        return Reading.unknown("in-progress probe returned an invalid kind")
    return Reading.known(value)


def _read_merge_base(
    root: Path,
    left: str,
    right: str,
    merge_base_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None,
) -> tuple[str | None, bool]:
    probe = merge_base_probe if merge_base_probe is not None else read_merge_base
    try:
        return probe(root, left, right)
    except Exception:
        return None, False


def _read_remote_sha(
    root: Path,
    remote: str,
    target: str,
    *,
    remote_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None,
) -> tuple[str | None, bool]:
    if remote_probe is not None:
        try:
            return remote_probe(root, remote, target)
        except Exception:
            return None, False
    ref_path = f"refs/remotes/{remote}/{target}"
    return _read_branch_sha(root, ref_path)


def _read_durable_record(
    root: Path,
) -> tuple[
    Reading[RecordReadOutcomeDisposition],
    IntegrationRecord | None,
    str | None,
]:
    try:
        outcome = read_record_outcome(root)
    except (OSError, ValueError) as exc:
        return (
            Reading.unknown(_brief_reason(str(exc) or type(exc).__name__)),
            None,
            None,
        )
    return (
        Reading.known(outcome.disposition),
        outcome.record,
        outcome.unreadable_reason,
    )


def _brief_reason(reason: str | None) -> str:
    if reason is None:
        return ""
    return reason.strip()[:_PROBE_REASON_MAX_LEN]


type _AncestryRelation = Literal["equal", "behind", "ahead", "diverged", "unknown"]

_TARGET_VS_FEATURE_BY_RELATION: Final[dict[str, TargetVsFeature]] = {
    "equal": TargetVsFeature.EQUAL,
    "behind": TargetVsFeature.BEHIND,
    "ahead": TargetVsFeature.AHEAD,
    "diverged": TargetVsFeature.DIVERGED,
    "unknown": TargetVsFeature.UNKNOWN,
}
_TARGET_VS_REMOTE_BY_RELATION: Final[dict[str, TargetVsRemote]] = {
    "equal": TargetVsRemote.EQUAL,
    "behind": TargetVsRemote.BEHIND,
    "ahead": TargetVsRemote.AHEAD,
    "diverged": TargetVsRemote.DIVERGED,
    "unknown": TargetVsRemote.UNKNOWN,
}


def _project_known_ancestry(
    root: Path,
    left: str,
    right: str,
    merge_base_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None,
) -> _AncestryRelation:
    if left == right:
        return "equal"
    merge_base, query_ok = _read_merge_base(
        root,
        left,
        right,
        merge_base_probe,
    )
    if not query_ok:
        return "unknown"
    if merge_base == left:
        return "behind"
    if merge_base == right:
        return "ahead"
    return "diverged"


def _project_target_vs_feature(
    root: Path,
    target_sha: tuple[str | None, bool],
    feature: Reading[str],
    merge_base_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None,
) -> TargetVsFeature:
    target_value, target_query_ok = target_sha
    feature_sha = feature.value if feature.is_known_good else None
    if not target_query_ok or target_value is None or feature_sha is None:
        return TargetVsFeature.UNKNOWN
    relation = _project_known_ancestry(
        root,
        target_value,
        feature_sha,
        merge_base_probe,
    )
    return _TARGET_VS_FEATURE_BY_RELATION[relation]


def _project_target_vs_remote(
    root: Path,
    remote_sha: tuple[str | None, bool],
    target_sha: tuple[str | None, bool],
    merge_base_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None,
) -> TargetVsRemote:
    remote_value, remote_query_ok = remote_sha
    target_value, target_query_ok = target_sha
    if not remote_query_ok or remote_value is None:
        return TargetVsRemote.UNREACHABLE
    if not target_query_ok or target_value is None:
        return TargetVsRemote.UNKNOWN
    relation = _project_known_ancestry(
        root,
        target_value,
        remote_value,
        merge_base_probe,
    )
    return _TARGET_VS_REMOTE_BY_RELATION[relation]


def _project_worktree(
    porcelain: Reading[str],
    in_progress_kind: InProgressKind,
) -> WorktreeState:
    if in_progress_kind in (
        InProgressKind.REBASE,
        InProgressKind.MERGE,
        InProgressKind.CHERRY_PICK,
    ):
        return WorktreeState.UNMERGED
    return classify_worktree(porcelain).state


def _project_head(head_state: Reading[HeadState]) -> HeadState:
    if not head_state.is_known_good:
        return HeadState.UNKNOWN
    value = head_state.value
    if value in (HeadState.ATTACHED, HeadState.DETACHED):
        return value
    return HeadState.UNKNOWN


def _record_claims_kind(record: IntegrationRecord, kind: InProgressKind) -> bool:
    claimed = classify_git_operation(record)
    if kind is InProgressKind.REBASE:
        return claimed in (
            GitOperationKind.RESOLVING_REBASE,
            GitOperationKind.AWAITING_CONTINUE,
        )
    if kind is InProgressKind.MERGE:
        return claimed in (
            GitOperationKind.RESOLVING_MERGE,
            GitOperationKind.AWAITING_MERGE_COMMIT,
        )
    return False


def _record_owned_here(root: Path, record: IntegrationRecord) -> bool:
    owner = record.owning_worktree
    if owner is None or not owner.strip():
        return True
    try:
        return Path(owner).resolve() == root.resolve()
    except (OSError, RuntimeError):
        return False


def _project_operation_ownership(
    root: Path,
    kind: InProgressKind,
    disposition: Reading[RecordReadOutcomeDisposition],
    record: IntegrationRecord | None,
) -> InProgressOwnership:
    if disposition.is_unknown:
        return InProgressOwnership.UNKNOWN
    disposition_value = disposition.value
    if disposition_value in (
        RecordReadOutcomeDisposition.BUSY,
        RecordReadOutcomeDisposition.UNREADABLE,
    ):
        return InProgressOwnership.UNKNOWN
    if record is None:
        return InProgressOwnership.FOREIGN
    if not _record_claims_kind(record, kind):
        return InProgressOwnership.FOREIGN
    if not _record_owned_here(root, record):
        return InProgressOwnership.FOREIGN
    return InProgressOwnership.OWNED


def _project_in_progress(
    root: Path,
    probe: Reading[InProgressKind],
    disposition: Reading[RecordReadOutcomeDisposition],
    record: IntegrationRecord | None,
) -> tuple[InProgressKind, InProgressOwnership]:
    if not probe.is_known_good:
        return InProgressKind.NONE, InProgressOwnership.UNKNOWN
    kind = probe.value
    if kind is None or kind not in tuple(InProgressKind):
        return InProgressKind.NONE, InProgressOwnership.UNKNOWN
    if kind is InProgressKind.NONE:
        return InProgressKind.NONE, InProgressOwnership.OWNED
    ownership = _project_operation_ownership(root, kind, disposition, record)
    return kind, ownership


def _record_agrees_with_observation(
    record: IntegrationRecord,
    in_progress_kind: InProgressKind,
    target_vs_feature: TargetVsFeature,
) -> bool:
    if record.phase == "integrated":
        return target_vs_feature in (
            TargetVsFeature.EQUAL,
            TargetVsFeature.AHEAD,
        )
    if in_progress_kind is InProgressKind.NONE:
        return False
    return _record_claims_kind(record, in_progress_kind)


def _present_disposition_state(
    root: Path,
    record: IntegrationRecord | None,
    *,
    target: str,
    in_progress_kind: InProgressKind,
    target_vs_feature: TargetVsFeature,
) -> IntegrationRecordState:
    if record is None:
        return IntegrationRecordState.STALE
    if not _record_owned_here(root, record):
        return IntegrationRecordState.FOREIGN
    if record.target != target:
        return IntegrationRecordState.STALE
    if not _record_agrees_with_observation(record, in_progress_kind, target_vs_feature):
        return IntegrationRecordState.STALE
    return IntegrationRecordState.CONSISTENT


def _unreadable_record_state(unreadable_reason: str | None) -> IntegrationRecordState:
    if unreadable_reason == _UNSUPPORTED_SCHEMA_REASON:
        return IntegrationRecordState.OLDER_VERSION
    return IntegrationRecordState.UNREADABLE


def _project_record(
    disposition: Reading[RecordReadOutcomeDisposition],
    record: IntegrationRecord | None,
    unreadable_reason: str | None,
    *,
    root: Path,
    target: str,
    in_progress_kind: InProgressKind,
    target_vs_feature: TargetVsFeature,
) -> IntegrationRecordState:
    if disposition.is_unknown:
        return IntegrationRecordState.UNREADABLE
    value = disposition.value
    if value is RecordReadOutcomeDisposition.ABSENT:
        return IntegrationRecordState.NONE
    if value is RecordReadOutcomeDisposition.UNREADABLE:
        return _unreadable_record_state(unreadable_reason)
    if value is RecordReadOutcomeDisposition.BUSY:
        return IntegrationRecordState.STALE
    if value is RecordReadOutcomeDisposition.PRESENT:
        return _present_disposition_state(
            root,
            record,
            target=target,
            in_progress_kind=in_progress_kind,
            target_vs_feature=target_vs_feature,
        )
    return IntegrationRecordState.STALE


def _build_observation(
    root: Path,
    *,
    target: str,
    remote: str,
    agent_active: bool,
    remote_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None,
    merge_base_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None,
    head_probe: Callable[[Path], Reading[HeadState]] | None,
    in_progress_probe: Callable[[Path], Reading[InProgressKind]] | None,
) -> Observation:
    porcelain = _read_porcelain(root)
    in_progress_reading = _read_in_progress_kind(root, in_progress_probe)
    head = _project_head(_read_head_state(root, head_probe))

    disposition, durable_record, unreadable_reason = _read_durable_record(root)
    in_progress_kind, in_progress_ownership = _project_in_progress(
        root,
        in_progress_reading,
        disposition,
        durable_record,
    )
    worktree = _project_worktree(
        porcelain,
        in_progress_kind,
    )

    target_sha = _read_branch_sha(root, target)
    feature = _read_head_sha(root)
    target_vs_feature = _project_target_vs_feature(
        root,
        target_sha,
        feature,
        merge_base_probe,
    )

    remote_sha = _read_remote_sha(
        root,
        remote,
        target,
        remote_probe=remote_probe,
    )
    target_vs_remote = _project_target_vs_remote(
        root,
        remote_sha,
        target_sha,
        merge_base_probe,
    )

    record = _project_record(
        disposition,
        durable_record,
        unreadable_reason,
        root=root,
        target=target,
        in_progress_kind=in_progress_kind,
        target_vs_feature=target_vs_feature,
    )

    return Observation(
        target_vs_feature=target_vs_feature,
        target_vs_remote=target_vs_remote,
        worktree=worktree,
        head=head,
        in_progress_kind=in_progress_kind,
        in_progress_ownership=in_progress_ownership,
        record=record,
        agent_active=agent_active,
    )


class IntegrationDirector:
    def __init__(
        self,
        root: Path,
        *,
        target: str = DEFAULT_TARGET_BRANCH,
        remote: str = DEFAULT_REMOTE,
        agent_active: bool = False,
        remote_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None = None,
        merge_base_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None = None,
        head_probe: Callable[[Path], Reading[HeadState]] | None = None,
        in_progress_probe: Callable[[Path], Reading[InProgressKind]] | None = None,
        unknown_budget: int = 3,
        repeat_threshold: int = 2,
    ) -> None:
        if unknown_budget < 1:
            raise ValueError("unknown_budget must be >= 1")
        if repeat_threshold < 1:
            raise ValueError("repeat_threshold must be >= 1")
        self._root = Path(root)
        self._target = target
        self._remote = remote
        self._agent_active = agent_active
        self._remote_probe = remote_probe
        self._merge_base_probe = merge_base_probe
        self._head_probe = head_probe
        self._in_progress_probe = in_progress_probe
        self._unknown_budget = unknown_budget
        self._repeat_threshold = repeat_threshold

    @property
    def root(self) -> Path:
        return self._root

    def observe(self) -> Observation:
        return _build_observation(
            self._root,
            target=self._target,
            remote=self._remote,
            agent_active=self._agent_active,
            remote_probe=self._remote_probe,
            merge_base_probe=self._merge_base_probe,
            head_probe=self._head_probe,
            in_progress_probe=self._in_progress_probe,
        )

    def observe_durable_record(self) -> Reading[RecordReadOutcomeDisposition]:
        disposition, _record, _unreadable_reason = _read_durable_record(self._root)
        return disposition

    def decide_and_act(
        self,
        root: Path | None = None,
        *,
        ledger_path: Path | None = None,
    ) -> DirectorDecision:
        del root
        observation = self.observe()
        decision_attempts = load_integration_attempts(
            self._root,
            ledger_path=ledger_path,
            unknown_budget=self._unknown_budget,
            repeat_threshold=self._repeat_threshold,
        )
        record_disposition = self.observe_durable_record()
        action = (
            NextAction.REPAIR
            if (
                record_disposition.value is RecordReadOutcomeDisposition.BUSY
                and not observation.has_unknown()
            )
            else decide(DecisionContext(observation=observation, attempts=decision_attempts))
        )
        charged = charge_integration_attempt(
            self._root,
            observation=observation,
            next_action=action,
            ledger_path=ledger_path,
            unknown_budget=self._unknown_budget,
            repeat_threshold=self._repeat_threshold,
        )
        release_applied = self._maybe_release_containment(observation)
        return DirectorDecision(
            action=action,
            observation=observation,
            attempts=charged,
            release_applied=release_applied,
        )

    def _maybe_release_containment(self, observation: Observation) -> bool:
        contained_ahead = observation.target_vs_feature in (
            TargetVsFeature.EQUAL,
            TargetVsFeature.AHEAD,
        )
        clean = observation.worktree is WorktreeState.CLEAN
        quiescent = observation.in_progress_kind is InProgressKind.NONE
        if not (contained_ahead and clean and quiescent):
            return False

        return self._release_retained_record()

    def _release_retained_record(self) -> bool:
        path = record_path(self._root)
        existed = path.exists()
        try:
            clear_record(self._root)
        except (OSError, RuntimeError):
            return False
        return existed and not path.exists()


def observe(root: Path) -> Observation:
    director = IntegrationDirector(root=root)
    return director.observe()
