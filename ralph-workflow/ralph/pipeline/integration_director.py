"""Single observation-driven owner for Ralph's auto-integration boundary.

This module is the one place that decides what integration does next for
a worktree. Every other integration seam — startup recovery, phase
boundary, planning gate, dispatch invariant, catch-up ticker, run-loop
retry wait — is a thin caller that asks the director and acts on its
verdict. The director's contract is the frozen :class:`DirectorDecision`
and the durable escalation ladder published in
:mod:`ralph.pipeline.integration_decision`.

The director performs three steps on every invocation:

1. **Observe** - build a frozen
   :class:`~ralph.pipeline.integration_observation.Observation` from
   live probes. Each probe (``porcelain``, merge-base ancestry,
   HEAD state, in-progress kind and ownership, target/feature/remote
   refs, record disposition) lifts exceptions into
   :class:`~ralph.pipeline.integration_observation.Reading.Unknown`
   via :func:`reading_from_callable` so an environmental failure never
   produces a blocking verdict. The seven
   :class:`~ralph.pipeline.integration_observation.Observation`
   dimensions are exactly the ones the decision table consumes.
2. **Load the ledger** - the restart-proof attempt ledger published
   in :mod:`ralph.pipeline.integration_attempt_ledger` is read once.
   A corrupt or older-version ledger is parsed leniently; the
   unknown-attempt budget is restored to whatever the file last
   persisted; the rung never resets upward.
3. **Decide** - the frozen
   :func:`~ralph.pipeline.integration_decision.decide` consumes
   the observation plus the ledger state and returns exactly one
   :class:`~ralph.pipeline.integration_decision.NextAction`. The
   bumped ledger state is persisted **before** any mutation.

When the observation proves the work has landed (feature ⊆ target and
a clean worktree) the director owns the **containment release**: any
durable integration record is cleared so the run is no longer
blocked. The release goes through
:func:`~ralph.pipeline.auto_integrate_record.clear_record` (the same
helper ``_owned_landing_reintegration`` uses) so the boundary sees a
fresh state in the same observation cycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal

from ralph.git.merge import observe_branch_sha
from ralph.git.operations import get_head_sha, is_repo_clean
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
    read_head_state,
    read_in_progress_kind,
    read_merge_base,
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
"""Default integration target branch; production wires ``release`` / ``tacos`` explicitly."""

DEFAULT_REMOTE: Final[str] = "origin"
"""Default remote name; production wires configured values explicitly."""

_PROBE_REASON_MAX_LEN: Final[int] = 120

#: The record read reports an unsupported schema when the on-disk record
#: uses a shape this build does not understand - the durable evidence of
#: a record written by an older Ralph version.
_UNSUPPORTED_SCHEMA_REASON: Final[str] = "unsupported_schema"

__all__ = [
    "DEFAULT_REMOTE",
    "DEFAULT_TARGET_BRANCH",
    "DirectorDecision",
    "IntegrationDirector",
    "observe",
]


# ---------------------------------------------------------------------------
# Director-side data carriers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DirectorDecision:
    """Frozen verdict returned by :meth:`IntegrationDirector.decide_and_act`.

    ``observation`` is the seven-dimension snapshot of live state at
    this cycle; ``attempts`` is the durable escalation-ladder state the
    director consulted (already bumped and persisted); ``action`` is
    the next step the table mapped for ``(observation, attempts)``;
    ``release_applied`` is ``True`` when the director cleared a
    retained record during this cycle (the containment release).
    """

    action: NextAction
    observation: Observation
    attempts: AttemptLedgerState
    release_applied: bool


# ---------------------------------------------------------------------------
# Probe helpers — each lifts exceptions into ``Reading.Unknown`` so
# environmental failures never reach the decision table as facts.
# ---------------------------------------------------------------------------


def _read_porcelain(root: Path) -> Reading[str]:
    """Run the porcelain probe through the ``reading_from_callable`` lift."""
    from ralph.git.subprocess_runner import run_git  # PLC0415: lazy import

    def _probe() -> str:
        result = run_git(
            ("status", "--porcelain", "--untracked-files=no"),
            cwd=root,
            label="integration-director-porcelain",
        )
        return result.stdout if result.returncode == 0 else ""

    return reading_from_callable(_probe)


def _read_branch_sha(root: Path, ref: str) -> tuple[str | None, bool]:
    """Read ``refs/heads/<ref>`` distinguishing absent from unreadable."""
    try:
        return observe_branch_sha(root, ref)
    except Exception:
        return None, False


def _read_head_sha(root: Path) -> Reading[str]:
    """Lift :func:`get_head_sha` into :class:`Reading`."""
    return reading_from_callable(lambda: get_head_sha(root))


def _read_head_state(
    root: Path,
    head_probe: Callable[[Path], Reading[HeadState]] | None,
) -> Reading[HeadState]:
    """Read the attached/detached HEAD state through an injectable probe."""
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
    """Read the operation kind through an injectable Git-marker probe."""
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
    """Read a merge base while preserving unreadable-versus-no-base semantics."""
    probe = merge_base_probe if merge_base_probe is not None else read_merge_base
    try:
        return probe(root, left, right)
    except Exception:
        return None, False


def _read_repo_clean(root: Path) -> bool:
    """Best-effort :func:`is_repo_clean` — return ``True`` on unreadable."""
    try:
        return bool(is_repo_clean(root))
    except Exception:
        return True


def _read_remote_sha(
    root: Path,
    remote: str,
    target: str,
    *,
    remote_probe: Callable[[Path, str, str], tuple[str | None, bool]] | None,
) -> tuple[str | None, bool]:
    """Read ``refs/remotes/<remote>/<target>``.

    The ``remote_probe`` callback lets tests substitute an in-memory
    answer; ``None`` uses :func:`observe_branch_sha` on the remote
    ref path (the production default).
    """
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
    """Read the durable integration record for the observation.

    Returns the typed disposition (lifted into :class:`Reading` so a
    raised read becomes ``Unknown``), the loaded record used to prove
    operation ownership and to project the record state, and the
    unreadable reason (when the disposition says the record could not
    be parsed).
    """
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
    """Render a probe explanation as a short, stable string."""
    if reason is None:
        return ""
    return reason.strip()[:_PROBE_REASON_MAX_LEN]


# ---------------------------------------------------------------------------
# Observation projection — map the probe readings onto the closed enum
# vocabulary the decision table consumes.
# ---------------------------------------------------------------------------


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
    """Classify two readable commit ids from their merge base."""
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
    """Project the (target, feature) SHA pair into the closed enum.

    The enum is oriented from the target's point of view: ``BEHIND``
    means the target is an ancestor of the feature, ``AHEAD`` means the
    feature is an ancestor of the target, and ``DIVERGED`` means both
    moved independently. An unreadable ref or merge-base probe is
    :attr:`TargetVsFeature.UNKNOWN`, never a guessed relationship.
    """
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
    """Project the (target, remote) SHA pair into the closed enum.

    The orientation matches :func:`_project_target_vs_feature`: target
    ``BEHIND`` means the remote is ahead, target ``AHEAD`` means the
    local target is ahead, and ``DIVERGED`` means neither contains the
    other. An unavailable remote remains ``UNREACHABLE``; an
    unreadable ancestry probe is ``UNKNOWN``.
    """
    remote_value, remote_query_ok = remote_sha
    target_value, target_query_ok = target_sha
    if not remote_query_ok:
        return TargetVsRemote.UNREACHABLE
    if not target_query_ok or target_value is None or remote_value is None:
        return TargetVsRemote.UNKNOWN
    relation = _project_known_ancestry(
        root,
        target_value,
        remote_value,
        merge_base_probe,
    )
    return _TARGET_VS_REMOTE_BY_RELATION[relation]


_PORCELAIN_CODE_LEN = 2
_UNTRACKED_CODE = "??"
_PORCELAIN_INDEX_STATUSES = frozenset({"M", "A", "D", "R", "C", "T"})
_PORCELAIN_WORKTREE_STATUSES = frozenset({"M", "D", "T"})


def _project_worktree(
    porcelain: Reading[str],
    in_progress_kind: InProgressKind,
    repo_clean: bool,
) -> WorktreeState:
    """Project the worktree probes into :class:`WorktreeState`.

    A known in-progress Git operation short-circuits to ``UNMERGED``
    because conflict markers or unmerged index entries are present in
    the states the integration boundary resumes. An unreadable
    operation probe is projected as ``NONE`` by the caller and therefore
    never becomes evidence of an unmerged tree. A readable porcelain
    with content is dispatched via the per-line classification; ``M ``
    in the second column beats ``A `` in the first column when both are
    present, and untracked ``?? `` lines map to ``UNTRACKED``.
    """
    if in_progress_kind in (
        InProgressKind.REBASE,
        InProgressKind.MERGE,
        InProgressKind.CHERRY_PICK,
    ):
        return WorktreeState.UNMERGED
    if not porcelain.is_known_good:
        return WorktreeState.UNKNOWN
    flags = _classify_porcelain(
        porcelain.value or "",
        code_len=_PORCELAIN_CODE_LEN,
        index_statuses=_PORCELAIN_INDEX_STATUSES,
        worktree_statuses=_PORCELAIN_WORKTREE_STATUSES,
    )
    selected = _select_worktree_state(flags, repo_clean)
    return selected


def _select_worktree_state(flags: dict[str, bool], repo_clean: bool) -> WorktreeState:
    """Map a per-line porcelain flag dict onto a closed :class:`WorktreeState`."""
    if flags["has_tracked"]:
        return WorktreeState.TRACKED_CHANGES
    if flags["has_staged"]:
        return WorktreeState.STAGED
    if flags["has_untracked"]:
        return WorktreeState.UNTRACKED
    return WorktreeState.CLEAN if repo_clean else WorktreeState.UNKNOWN


def _classify_porcelain(
    text: str,
    *,
    code_len: int,
    index_statuses: frozenset[str],
    worktree_statuses: frozenset[str],
) -> dict[str, bool]:
    """Classify porcelain output into a typed dict."""
    has_staged = False
    has_tracked = False
    has_untracked = False
    for raw_line in text.splitlines():
        if len(raw_line) < code_len:
            continue
        head = raw_line[:code_len]
        if head == _UNTRACKED_CODE:
            has_untracked = True
            continue
        if head[0] in index_statuses:
            has_staged = True
        if head[1] in worktree_statuses:
            has_tracked = True
    return {
        "has_staged": has_staged,
        "has_tracked": has_tracked,
        "has_untracked": has_untracked,
    }


def _project_head(head_state: Reading[HeadState]) -> HeadState:
    """Project the HEAD probe into :class:`HeadState`."""
    if not head_state.is_known_good:
        return HeadState.UNKNOWN
    value = head_state.value
    if value in (HeadState.ATTACHED, HeadState.DETACHED):
        return value
    return HeadState.UNKNOWN


def _record_claims_kind(record: IntegrationRecord, kind: InProgressKind) -> bool:
    """Return whether the durable record claims the observed operation kind."""
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
    """Return whether the record's owning worktree is the observed worktree."""
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
    """Project durable-record evidence into operation ownership."""
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
    """Project the operation probe and durable record into kind/ownership.

    An unreadable operation probe is ``NONE`` plus
    :attr:`InProgressOwnership.UNKNOWN`: it is never evidence that an
    operation exists and never blocks the integration boundary. For a
    known operation, ownership is proved only when the durable record
    claims the same kind and names this worktree (or predates explicit
    worktree ownership). A missing claim, a different claimed kind, or
    another worktree's owner is foreign.
    """
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
    """Return whether the durable record contradicts the observed Git state.

    Git outranks the record: a record that claims something the
    observation does not show is stale intent, not a fact. The two
    claims a record makes are checked:

    * an ``integrated`` record claims the replay finished, so the
      feature must be contained in the target;
    * an ``integrating`` record claims a replay is in flight, so Git
      must show the operation the record claims.
    """
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
    """Map a ``PRESENT`` disposition into the closed record enum.

    A present record is projected by what it says about the workspace,
    never by the mere fact that it exists:

    * a record another worktree owns is :attr:`IntegrationRecordState.FOREIGN`;
    * a record about another target, or one whose claims the observed
      Git state contradicts, is :attr:`IntegrationRecordState.STALE`
      (stale intent);
    * a record that agrees with the observation is
      :attr:`IntegrationRecordState.CONSISTENT`.
    """
    if record is None:
        return IntegrationRecordState.STALE
    if not _record_owned_here(root, record):
        return IntegrationRecordState.FOREIGN
    if record.target != target:
        return IntegrationRecordState.STALE
    if not _record_agrees_with_observation(record, in_progress_kind, target_vs_feature):
        return IntegrationRecordState.STALE
    return IntegrationRecordState.CONSISTENT


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
    """Project the durable-record probes into :class:`IntegrationRecordState`.

    The mapping is conservative: a non-absent record with a retained
    reason is treated as :attr:`IntegrationRecordState.STALE` so the
    decision table can consult the durable-receipt kind without ever
    reading it as a fact. An unreadable record whose SCHEMA this build
    does not understand is
    :attr:`IntegrationRecordState.OLDER_VERSION` (the record predates
    this build); an unreadable record that is merely corrupt stays
    :attr:`IntegrationRecordState.UNREADABLE`.
    """
    if disposition.is_unknown:
        return IntegrationRecordState.UNREADABLE
    value = disposition.value
    if value is RecordReadOutcomeDisposition.ABSENT:
        return IntegrationRecordState.NONE
    if value is RecordReadOutcomeDisposition.UNREADABLE:
        if unreadable_reason == _UNSUPPORTED_SCHEMA_REASON:
            return IntegrationRecordState.OLDER_VERSION
        return IntegrationRecordState.UNREADABLE
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


# ---------------------------------------------------------------------------
# Top-level observation builder + helper.
# ---------------------------------------------------------------------------


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
    """Assemble the seven-dimension :class:`Observation` for ``root``.

    Each probe is lifted into a three-valued reading so an
    environmental failure never reaches the decision table as a fact;
    the ``UNKNOWN`` enum members route the verdict to
    :attr:`NextAction.WAIT_FOR_ENVIRONMENT` per decision-table rule 1.
    """
    porcelain = _read_porcelain(root)
    in_progress_reading = _read_in_progress_kind(root, in_progress_probe)
    head = _project_head(_read_head_state(root, head_probe))
    repo_clean = _read_repo_clean(root)

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
        repo_clean,
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

    record = _project_record(disposition, reason)

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


# ---------------------------------------------------------------------------
# Director.
# ---------------------------------------------------------------------------


class IntegrationDirector:
    """Observation-driven owner of every integration decision.

    The director is the only consumer of the integration decision
    table; every other integration seam obtains its action through
    :meth:`decide_and_act`. The director is also the only writer of
    the attempt ledger via the write-ahead high-water mark applied
    before any mutation.
    """

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
        """Bind an :class:`IntegrationDirector` to ``root``.

        Args:
            root: The worktree / workspace root this director
                observes.
            target: The configured integration target branch name.
                Production callers pass ``release`` / ``tacos`` /
                configured values; tests pass the default
                :data:`DEFAULT_TARGET_BRANCH`.
            remote: The configured remote name (defaults to
                :data:`DEFAULT_REMOTE`; same caveat applies).
            agent_active: Whether an agent phase is currently active
                in the worktree. Recorded on the
                :class:`Observation`.
            remote_probe: Optional remote probe override for tests;
                accepts ``(root, remote, target)`` and returns
                ``(sha, ok)``. ``None`` means use the production
                probe chain.
            merge_base_probe: Optional ancestry probe override;
                accepts ``(root, left, right)`` and returns
                ``(base_sha, ok)``. ``base_sha=None`` with ``ok=True``
                proves that no common ancestor exists; ``ok=False``
                reports an unreadable probe.
            head_probe: Optional attached/detached HEAD probe override;
                accepts ``(root)`` and returns a
                :class:`~ralph.pipeline.integration_observation.Reading`.
            in_progress_probe: Optional operation-kind probe override;
                accepts ``(root)`` and returns a
                :class:`~ralph.pipeline.integration_observation.Reading`.
            unknown_budget: Default unknown-attempt budget.
            repeat_threshold: Default repeat-count threshold.
        """
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
        """The directory this director observes."""
        return self._root

    def observe(self) -> Observation:
        """Return the live seven-dimension observation."""
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
        """Return the durable record disposition reading for tests.

        Public so focused tests can assert the disposition path the
        director consumed without having to read :func:`observe` and
        fish the dimension out of the seven-dimension snapshot.
        """
        disposition, _reason, _record = _read_durable_record(self._root)
        return disposition

    def decide_and_act(
        self,
        root: Path | None = None,
        *,
        ledger_path: Path | None = None,
    ) -> DirectorDecision:
        """Observe once, delegate the charge, release containment, return verdict.

        The durable attempt ledger is read and charged through
        :mod:`ralph.pipeline.integration_owner` - the single charge
        boundary. The director never writes the ledger itself, so
        exactly ONE persisted charge exists per mutation-class
        decision, whether the decision came from here or from the
        owner's own verdict seam.

        Args:
            root: Reserved for caller symmetry; ``None`` reuses the
                director's bound :attr:`root`.
            ledger_path: Override the attempt-ledger file path.
                ``None`` reuses the director's default under
                ``<root>/.agent/integration_attempt_ledger.json``.

        Returns:
            A frozen :class:`DirectorDecision` carrying the next
            :class:`NextAction`, the live :class:`Observation`, the
            charged ledger state, and whether the containment release
            fired this cycle.
        """
        del root  # reserved for caller symmetry; the bound root is canonical.
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
        """Clear the retained record when the work has provably landed.

        Returns:
            ``True`` only when the on-disk record was actually
            cleared during this cycle. ``False`` whenever the
            observation does not satisfy the containment gate, the
            verdict says "not contained", or the clear call raised.
        """
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
        """Unlink any retained durable record under ``self._root``.

        Uses :func:`~ralph.pipeline.auto_integrate_record.clear_record`
        — the same helper :mod:`ralph.pipeline._owned_landing_reintegration`
        uses — and confirms the on-disk file is gone before claiming
        success. Returns ``False`` for every failure mode (absent,
        unreadable, busy, raised).
        """
        path = record_path(self._root)
        existed = path.exists()
        try:
            clear_record(self._root)
        except (OSError, RuntimeError):
            return False
        return existed and not path.exists()


def observe(root: Path) -> Observation:
    """Build the seven-dimension :class:`Observation` for ``root``.

    Exposed for tests that need the raw observation without paying for
    the ledger round-trip or the containment release. Delegates to the
    default :class:`IntegrationDirector` instance so the helper and
    the director probe the same underlying git state.
    """
    director = IntegrationDirector(root=root)
    return director.observe()
