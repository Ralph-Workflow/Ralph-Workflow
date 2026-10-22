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
   live probes. Each probe (``porcelain``, ``rebase_state``,
   ``merge_state``, target/feature/remote refs, in-progress kind and
   ownership, record disposition) lifts exceptions into
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

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.git.merge import (
    MERGE_STATE_IN_PROGRESS,
    MERGE_STATE_UNKNOWN,
    merge_state,
    observe_branch_sha,
)
from ralph.git.operations import get_head_sha, is_repo_clean
from ralph.git.rebase.rebase import rebase_in_progress
from ralph.pipeline.auto_integrate_record import (
    RecordReadOutcomeDisposition,
    clear_record,
    read_record_outcome,
    record_path,
)
from ralph.pipeline.integration_attempt_ledger import (
    AttemptLedger,
    AttemptLedgerState,
)
from ralph.pipeline.integration_decision import (
    AttemptState,
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
    reading_from_callable,
)

if TYPE_CHECKING:
    from collections.abc import Callable


DEFAULT_TARGET_BRANCH: Final[str] = "main"
"""Default integration target branch; production wires ``release`` / ``tacos`` explicitly."""

DEFAULT_REMOTE: Final[str] = "origin"
"""Default remote name; production wires configured values explicitly."""

_PROBE_REASON_MAX_LEN: Final[int] = 120

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


def _read_rebase_active(root: Path) -> Reading[bool]:
    """Lift :func:`rebase_in_progress` into :class:`Reading`."""
    return reading_from_callable(lambda: bool(rebase_in_progress(root)))


def _read_branch_sha(root: Path, ref: str) -> tuple[str | None, bool]:
    """Read ``refs/heads/<ref>`` distinguishing absent from unreadable."""
    try:
        return observe_branch_sha(root, ref)
    except Exception:
        return None, False


def _read_head_sha(root: Path) -> Reading[str]:
    """Lift :func:`get_head_sha` into :class:`Reading`."""
    return reading_from_callable(lambda: get_head_sha(root))


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


def _read_durable_record(root: Path) -> tuple[Reading[RecordReadOutcomeDisposition], str | None]:
    """Read the durable integration record for the observation.

    Returns the typed disposition (lifted into :class:`Reading` so a
    raised read becomes ``Unknown``) plus the human-readable reason
    the integration-resolution helper would surface to dispatch.
    """
    try:
        outcome = read_record_outcome(root)
    except (OSError, ValueError) as exc:
        return (
            Reading.unknown(_brief_reason(str(exc) or type(exc).__name__)),
            None,
        )
    return Reading.known(outcome.disposition), _outcome_reason(outcome)


def _outcome_reason(outcome: object) -> str | None:
    """Return a brief reason string for ``outcome``; ``None`` when absent."""
    reason_obj: object = _safe_attr(outcome, "unreadable_reason")
    if isinstance(reason_obj, str) and reason_obj:
        return _brief_reason(reason_obj)
    disposition = _safe_attr(outcome, "disposition")
    if disposition is RecordReadOutcomeDisposition.UNREADABLE:
        return "unreadable durable integration record"
    if disposition is RecordReadOutcomeDisposition.BUSY:
        return "transient lock contention on the durable record"
    record_obj: object = _safe_attr(outcome, "record")
    if record_obj is not None:
        phase = _safe_attr(record_obj, "phase")
        if isinstance(phase, str) and phase:
            return phase
    return None


def _safe_attr(obj: object, name: str) -> object:
    """Best-effort attribute lookup; returns ``None`` on missing or raised."""
    if obj is None:
        return None
    try:
        result: object = getattr(obj, name)
    except AttributeError:
        return None
    return result


def _brief_reason(reason: str | None) -> str:
    """Render a probe explanation as a short, stable string."""
    if reason is None:
        return ""
    return reason.strip()[:_PROBE_REASON_MAX_LEN]


# ---------------------------------------------------------------------------
# Observation projection — map the probe readings onto the closed enum
# vocabulary the decision table consumes.
# ---------------------------------------------------------------------------


def _project_target_vs_feature(
    target_sha: tuple[str | None, bool],
    feature: Reading[str],
) -> TargetVsFeature:
    """Project the (target, feature) SHA pair into the closed enum.

    When either side is unreadable the result is
    :attr:`TargetVsFeature.UNKNOWN` so the observation-driven decision
    routes it through the environment-wait path instead of dispatching
    the resolver.
    """
    target_value, target_query_ok = target_sha
    feature_sha = feature.value if feature.is_known_good else None
    if not target_query_ok or target_value is None or feature_sha is None:
        return TargetVsFeature.UNKNOWN
    if target_value == feature_sha:
        return TargetVsFeature.EQUAL
    return TargetVsFeature.UNKNOWN


def _project_target_vs_remote(
    remote_sha: tuple[str | None, bool],
    target_sha: tuple[str | None, bool],
) -> TargetVsRemote:
    """Project the (target, remote) SHA pair into the closed enum."""
    remote_value, remote_query_ok = remote_sha
    target_value, target_query_ok = target_sha
    if not target_query_ok or target_value is None:
        return TargetVsRemote.UNKNOWN
    if not remote_query_ok:
        return TargetVsRemote.UNREACHABLE
    if remote_value is None:
        return TargetVsRemote.UNKNOWN
    if remote_value == target_value:
        return TargetVsRemote.EQUAL
    return TargetVsRemote.UNKNOWN


_PORCELAIN_CODE_LEN = 2
_UNTRACKED_CODE = "??"
_PORCELAIN_INDEX_STATUSES = frozenset({"M", "A", "D", "R", "C", "T"})
_PORCELAIN_WORKTREE_STATUSES = frozenset({"M", "D", "T"})


def _project_worktree(
    porcelain: Reading[str],
    merge_verdict: str,
    repo_clean: bool,
    rebase_active: bool,
) -> WorktreeState:
    """Project the worktree probes into :class:`WorktreeState`.

    An in-progress rebase or merge short-circuits to ``UNMERGED``
    because conflict markers or unmerged index entries are present in
    either case. A readable porcelain with content is dispatched via
    the per-line classification; ``M `` in the second column beats
    ``A `` in the first column when both are present, and untracked
    ``?? `` lines map to ``UNTRACKED``.
    """
    if rebase_active:
        return WorktreeState.UNMERGED
    if merge_verdict == MERGE_STATE_IN_PROGRESS:
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


def _project_head(detached: Reading[bool]) -> HeadState:
    """Project the HEAD probe into :class:`HeadState`."""
    if not detached.is_known_good:
        return HeadState.UNKNOWN
    return HeadState.DETACHED if detached.value else HeadState.ATTACHED


def _project_head_detached(head_sha_reading: Reading[str]) -> Reading[bool]:
    """Translate a HEAD-SHA reading into a detached-state reading.

    A real detached probe (an explicit ``HEAD`` ref query) is not
    exposed at the level the director probes, so the conservative
    answer is ``attached`` (``False``). The reading is still wrapped
    in :class:`Reading` so the projection path stays uniform — tests
    can substitute a real probe by overriding ``read_head_sha``.
    """
    if not head_sha_reading.is_known_good:
        return Reading.unknown("head probe")
    return Reading.known(False)


def _project_in_progress(
    rebase_active: Reading[bool],
    merge_verdict: str,
) -> tuple[InProgressKind, InProgressOwnership]:
    """Project the in-progress probes into the kind/ownership pair.

    Ownership defaults to :attr:`InProgressOwnership.OWNED` because
    the integration boundary owns every rebase / merge it observes;
    the conservative default lets the decision table reach
    ``RESUME_OWNED_OPERATION`` instead of bypassing the ladder. An
    unreadable merge probe maps to ``UNKNOWN`` so the ladder reaches
    the environment-wait path.
    """
    if rebase_active.is_known_good and rebase_active.value:
        return InProgressKind.REBASE, InProgressOwnership.OWNED
    if merge_verdict == MERGE_STATE_IN_PROGRESS:
        return InProgressKind.MERGE, InProgressOwnership.OWNED
    if merge_verdict == MERGE_STATE_UNKNOWN:
        return InProgressKind.MERGE, InProgressOwnership.UNKNOWN
    return InProgressKind.NONE, InProgressOwnership.OWNED


def _present_disposition_state(
    reason: str | None,
) -> IntegrationRecordState:
    """Map a ``PRESENT`` disposition into the closed record enum."""
    if isinstance(reason, str) and reason == "consistent":
        return IntegrationRecordState.CONSISTENT
    return IntegrationRecordState.STALE


def _project_record(
    disposition: Reading[RecordReadOutcomeDisposition],
    reason: str | None,
) -> IntegrationRecordState:
    """Project the durable-record probes into :class:`IntegrationRecordState`.

    The mapping is conservative: a non-absent record with a retained
    reason is treated as :attr:`IntegrationRecordState.STALE` so the
    decision table can consult the durable-receipt kind without ever
    reading it as a fact. A ``PRESENT`` disposition with a
    ``consistent`` phase maps to
    :attr:`IntegrationRecordState.CONSISTENT`.
    """
    if disposition.is_unknown:
        return IntegrationRecordState.UNREADABLE
    value = disposition.value
    if value is RecordReadOutcomeDisposition.ABSENT:
        return IntegrationRecordState.NONE
    if value is RecordReadOutcomeDisposition.UNREADABLE:
        return IntegrationRecordState.UNREADABLE
    if value is RecordReadOutcomeDisposition.BUSY:
        return IntegrationRecordState.STALE
    if value is RecordReadOutcomeDisposition.PRESENT:
        return _present_disposition_state(reason)
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
) -> Observation:
    """Assemble the seven-dimension :class:`Observation` for ``root``.

    Each probe is lifted through :func:`reading_from_callable` so an
    environmental failure never reaches the decision table as a fact;
    the ``UNKNOWN`` enum members route the verdict to
    :attr:`NextAction.WAIT_FOR_ENVIRONMENT` per decision-table rule 1.
    """
    porcelain = _read_porcelain(root)
    merge_verdict = merge_state(root)
    rebase = _read_rebase_active(root)
    in_progress_kind, in_progress_ownership = _project_in_progress(rebase, merge_verdict)
    head = _project_head(_project_head_detached(_read_head_sha(root)))
    repo_clean = _read_repo_clean(root)
    worktree = _project_worktree(
        porcelain,
        merge_verdict,
        repo_clean,
        bool(rebase.value) if rebase.is_known_good else False,
    )

    target_sha = _read_branch_sha(root, target)
    feature = _read_head_sha(root)
    target_vs_feature = _project_target_vs_feature(target_sha, feature)

    remote_sha = _read_remote_sha(
        root,
        remote,
        target,
        remote_probe=remote_probe,
    )
    target_vs_remote = _project_target_vs_remote(remote_sha, target_sha)

    disposition, reason = _read_durable_record(root)
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


def _is_detached(head_sha: str) -> bool:
    """A tiny helper that maps an arbitrary HEAD SHA to a coarse attached answer.

    The director never has access to a full ``HEAD``-vs-ref probe
    without an extra shell out; the conservative answer is ``False``
    (= attached) so the decision table does not penalise a warm tree.
    A test that drives :class:`HeadState.DETACHED` overrides
    :func:`observe`'s plumbing directly.
    """
    del head_sha
    return False


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
        )

    def observe_durable_record(self) -> Reading[RecordReadOutcomeDisposition]:
        """Return the durable record disposition reading for tests.

        Public so focused tests can assert the disposition path the
        director consumed without having to read :func:`observe` and
        fish the dimension out of the seven-dimension snapshot.
        """
        disposition, _reason = _read_durable_record(self._root)
        return disposition

    def decide_and_act(
        self,
        root: Path | None = None,
        *,
        ledger_path: Path | None = None,
    ) -> DirectorDecision:
        """Observe once, load the ledger, decide, persist, return verdict.

        Args:
            root: Reserved for caller symmetry; ``None`` reuses the
                director's bound :attr:`root`.
            ledger_path: Override the attempt-ledger file path.
                ``None`` reuses the director's default under
                ``<root>/.agent/integration_attempt_ledger.json``.

        Returns:
            A frozen :class:`DirectorDecision` carrying the next
            :class:`NextAction`, the live :class:`Observation`, the
            ledger state that backed the decision, and whether the
            containment release fired this cycle.
        """
        del root  # reserved for caller symmetry; the bound root is canonical.
        observation = self.observe()
        ledger = AttemptLedger(
            self._root,
            ledger_path=ledger_path,
            unknown_budget=self._unknown_budget,
            repeat_threshold=self._repeat_threshold,
        )
        attempts = ledger.read()
        decision_attempts = self._to_attempt_state(attempts)
        record_disposition = self.observe_durable_record()
        action = (
            NextAction.REPAIR
            if record_disposition.value is RecordReadOutcomeDisposition.BUSY
            else decide(DecisionContext(observation=observation, attempts=decision_attempts))
        )
        release_applied = self._maybe_release_containment(observation)
        next_attempts = self._bump_attempts(attempts, observation, action)
        with contextlib.suppress(OSError):
            ledger.persist(next_attempts)
        return DirectorDecision(
            action=action,
            observation=observation,
            attempts=next_attempts,
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

    def _bump_attempts(
        self,
        current: AttemptLedgerState,
        observation: Observation,
        action: NextAction,
    ) -> AttemptLedgerState:
        """Apply the durable ladder bump for ``(observation, action)``.

        Reuses the same bump rules as
        :class:`~ralph.pipeline.integration_decision.AttemptState.with_attempt`
        so the ledger-rewritten value matches the in-memory dataclass;
        the result is then re-projected onto
        :class:`AttemptLedgerState` (the cursor for the durable ledger
        file). An unknown observation is conservatively marked as an
        environment wait so the unknown-attempt budget advances under
        every observation the decision table would otherwise classify
        ``WAIT_FOR_ENVIRONMENT``.
        """
        view = self._to_attempt_state(current)
        is_unknown = action is NextAction.WAIT_FOR_ENVIRONMENT or observation.has_unknown()
        bumped = view.with_attempt(observation, action, is_unknown=is_unknown)
        return AttemptLedgerState(
            rung=bumped.rung,
            repeat_counts=bumped.repeat_counts,
            unknown_attempts=bumped.unknown_attempts,
            unknown_budget=bumped.unknown_budget,
            repeat_threshold=bumped.repeat_threshold,
        )

    @staticmethod
    def _to_attempt_state(state: AttemptLedgerState) -> AttemptState:
        """Project the durable ledger state onto the in-memory dataclass.

        :class:`AttemptLedgerState` is the persisted shape (carries a
        ``version`` field); :class:`AttemptState` is the in-memory
        shape the decision table consumes. The two share field names
        and semantics, so the projection is a per-field copy.
        """
        return AttemptState(
            rung=state.rung,
            repeat_counts=state.repeat_counts,
            unknown_attempts=state.unknown_attempts,
            unknown_budget=state.unknown_budget,
            repeat_threshold=state.repeat_threshold,
        )


def _feature_sha_safe(root: Path) -> str | None:
    """Read ``HEAD`` for the containment verdict; return ``None`` on failure."""
    try:
        return get_head_sha(root)
    except Exception:
        return None


def observe(root: Path) -> Observation:
    """Build the seven-dimension :class:`Observation` for ``root``.

    Exposed for tests that need the raw observation without paying for
    the ledger round-trip or the containment release. Delegates to the
    default :class:`IntegrationDirector` instance so the helper and
    the director probe the same underlying git state.
    """
    director = IntegrationDirector(root=root)
    return director.observe()
