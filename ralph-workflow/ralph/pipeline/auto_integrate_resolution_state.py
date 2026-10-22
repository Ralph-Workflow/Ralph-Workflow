"""Release durable unresolved integration evidence at optional integration seams.

Preserving that evidence and REFUSING TO RETRY are two different things,
and conflating them deadlocks the run. The integration seam is not
ordinary work that must wait for a conflict to be resolved -- it is the
thing that resolves it. Gating it on the same verdict that gates
ordinary phase dispatch leaves the only operation able to clear the
verdict blocked by the verdict, so conflict outcome latches
for the rest of the run and
:func:`~ralph.pipeline.integration_resolution.assert_non_resolution_dispatch_allowed`
then rejects every phase -- against a repository that may already be
clean.

The split this module encodes:

* ORDINARY dispatch stays fail-closed on the verdict. Untouched.
* The integration seam always runs, and its OUTCOME is filtered through
  :func:`preserve_unresolved_resolution_state`, so a seam that did not
  land cannot launder a recorded conflict into a "skip".
* ONE verdict -- :func:`evaluate_integration_containment` -- decides
  whether the feature's commits are already inside the target, and
  :func:`ralph.pipeline.auto_integrate_record.apply_containment_release`
  is the only writer that clears the record and its latches. Git is the
  ground truth: a durable record carries intent and history, so a record
  claiming "unresolved" may never outrank a live
  ``merge-base --is-ancestor`` answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_record import apply_containment_release
from ralph.pipeline.integration_resolution import persisted_integration_resolution_verdict
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from pathlib import Path

CONFLICT_RESOLUTION_STRATEGIES = (
    "rebase_resolver",
    "refresh_retry",
    "merge_instead",
    "resolver_with_history",
)

__all__ = [
    "CONFLICT_RESOLUTION_STRATEGIES",
    "ContainmentVerdict",
    "contained_land",
    "containment_release",
    "evaluate_integration_containment",
    "preserve_unresolved_resolution_state",
    "reconcile_stale_unresolved_state",
    "retains_unresolved_resolution_state",
]


@dataclass(frozen=True, slots=True)
class ContainmentVerdict:
    """Read-only answer: does ``<target>`` already hold every feature commit?

    Attributes:
        contained: Whether no feature commit sits beyond the target.
        target: The integration target branch the verdict was asked about.
        feature_sha: The feature tip the verdict tested.
        target_sha: The observed target SHA, or ``None`` when unreadable.
        evidence: Which source answered, for the operator log.
    """

    contained: bool
    target: str | None
    feature_sha: str | None
    target_sha: str | None
    evidence: str

    @property
    def reason(self) -> str:
        """Operator-facing sentence naming the verdict and its evidence."""
        answer = "contains" if self.contained else "does not contain"
        return f"'{self.target}' {answer} feature tip {self.feature_sha} ({self.evidence})"


def evaluate_integration_containment(
    root: Path | None,
    target: str | None,
    *,
    feature_sha: str | None = None,
    proven: bool = False,
) -> ContainmentVerdict:
    """Return the single containment verdict for ``root`` and ``target``.

    Args:
        root: The repository to ask, or ``None`` when the caller has none.
        target: The integration target branch name.
        feature_sha: The feature tip to test; ``root``'s HEAD when omitted.
        proven: The caller already holds direct proof the work landed -- a
            fast-forward that returned, or a live inspection that came back
            clean. It counts only while no repository is named: once git
            can answer, git decides and ``proven`` cannot overrule it.

    An unanswerable question is never containment. An unreadable
    repository or an unknown branch keeps every latch, because releasing
    on a guess discards unfinished work while holding it discards nothing.
    """
    target_name = target or ""
    if root is None or not target_name:
        return ContainmentVerdict(
            proven,
            target_name,
            feature_sha,
            None,
            "caller proved the work already landed" if proven else "no repository named",
        )
    from ralph.git.merge import MERGE_STATE_IN_PROGRESS, branch_sha, merge_state
    from ralph.git.merge_obstructions import ancestry_state
    from ralph.git.operations import get_head_sha
    from ralph.git.rebase.rebase import rebase_in_progress

    try:
        # A paused rebase or merge detaches HEAD onto target-side commits,
        # so HEAD is no longer the feature tip and an ancestry answer about
        # it proves nothing about the branch being integrated. Fail closed:
        # recovery owns the seam until the operation finishes.
        if rebase_in_progress(root) or merge_state(root) == MERGE_STATE_IN_PROGRESS:
            return ContainmentVerdict(
                False, target_name, feature_sha, None, "integration operation in flight"
            )
        head = feature_sha or get_head_sha(root)
        target_sha = branch_sha(root, target_name)
        answer = ancestry_state(root, head, target_sha or target_name)
    except Exception as exc:
        logger.warning("auto_integrate: containment query on '{}' failed: {}", target_name, exc)
        return ContainmentVerdict(
            False, target_name, feature_sha, None, f"git could not answer ancestry: {exc}"
        )
    return ContainmentVerdict(
        answer is True,
        target_name,
        head,
        target_sha,
        "git merge-base --is-ancestor" if answer is not None else "git could not answer ancestry",
    )


def containment_release(
    root: Path | None,
    target: str | None,
    state: RebaseState,
    *,
    proven: bool = False,
) -> RebaseState:
    """Return ``state`` with every containment-proven latch cleared."""
    return apply_containment_release(
        state, root, evaluate_integration_containment(root, target, proven=proven)
    )


def contained_land(root: Path, target: str, *, require_in_sync: bool = False) -> RebaseState | None:
    """Return the no-op success for an already-contained land, else ``None``.

    ``require_in_sync`` is for callers that must also absorb the target
    (planning catch-up): a feature merely *inside* a target that has moved
    ahead is not in sync, so there the shortcut is refused and the real
    integration runs.

    Landing is idempotent: once every feature commit is inside the target
    there is nothing to rebase, merge, or move, so the second land must be
    a success that changes nothing. ``None`` means "not contained" and the
    caller must land for real.
    """
    verdict = evaluate_integration_containment(root, target)
    if not verdict.contained:
        return None
    if require_in_sync:
        from ralph.git.branch_relation import branch_relation

        if branch_relation(root, target).needs_catch_up:
            return None
    logger.info("auto_integrate: {}", verdict.reason)
    return apply_containment_release(
        RebaseState(
            last_outcome=RebaseOutcomeKind.FAST_FORWARDED,
            last_target=target,
            fast_forwarded=True,
        ),
        root,
        verdict,
    )


def reconcile_stale_unresolved_state(
    state: RebaseState,
    *,
    root: Path | None = None,
    target: str | None = None,
) -> RebaseState:
    """Release stale conflict evidence through the single containment verdict.

    A caller that can name a repository hands it to the verdict, which then
    asks git and may therefore retain evidence the caller once cleared
    blindly. A caller without one is stating that its live
    ``inspect_integration_resolution`` inspection came back clean; that
    inspection IS the proof the verdict consumes.
    """
    return containment_release(root, target, state, proven=True)


def retains_unresolved_resolution_state(state: RebaseState) -> bool:
    """Return whether durable integration evidence owns the phase boundary."""
    return persisted_integration_resolution_verdict(state) is not None


def preserve_unresolved_resolution_state(
    outcome: RebaseState | None,
    *,
    prior: RebaseState,
) -> RebaseState | None:
    """Carry unresolved integration evidence across a seam that did not land.

    A seam run against an already-unresolved integration has exactly
    three honest endings:

    * it LANDED (``fast_forwarded``), so the conflict is genuinely gone,
      the outcome stands as written, and every latch is released through
      the containment verdict;
    * it recorded a conflict of its own, so the outcome already carries
      the evidence; or
    * it did neither -- a skip (dirty worktree, target unmoved, detached
      HEAD). Nothing about the repository changed, so the earlier
      evidence is still true and must survive. A skip that overwrote
      ``last_action='conflict'`` with ``'skip'`` is exactly how an
      unresolved integration used to become invisible to
      :func:`retains_unresolved_resolution_state`, letting ordinary
      phases dispatch onto a conflicted tree.

    ``resolution_exhausted`` rides the same rule. It is set in one place
    and, before this, cleared in none -- so one exhausted resolver chain
    made every later run and every ``--resume`` exit against a clean
    tree, permanently, recoverable only by hand-editing
    ``.agent/checkpoint.json``. A landing is the reset it never had.

    Args:
        outcome: What the seam recorded, or ``None`` when it did nothing.
        prior: The state the seam was entered with.

    Returns:
        ``outcome`` when it landed or already carries evidence; ``prior``
        when the seam recorded nothing and ``prior`` still holds
        evidence; otherwise ``outcome`` with the prior evidence restored.
    """
    if outcome is None:
        return prior if retains_unresolved_resolution_state(prior) else None
    if outcome.fast_forwarded:
        # A landed fast-forward moved the target onto the feature tip, so
        # the conflict really is gone: the verdict releases every latch,
        # not just the retry counter.
        return containment_release(None, outcome.last_target, outcome, proven=True)
    if not retains_unresolved_resolution_state(prior):
        return outcome

    update: dict[str, object] = {}
    if prior.integration_retry_attempt != outcome.integration_retry_attempt:
        update["integration_retry_attempt"] = prior.integration_retry_attempt
    if prior.resolution_exhausted and not outcome.resolution_exhausted:
        update["resolution_exhausted"] = True
        update["resolution_exhaustion_reason"] = prior.resolution_exhaustion_reason
    if prior.integration_unresolved and not outcome.integration_unresolved:
        # Carry the BLOCK, not the label. Rewriting ``last_action`` to
        # "conflict" would make the state lie about what this seam did --
        # it skipped -- and the operator log and the budget tests both
        # read that field for the honest answer.
        update["unresolved_integration_carried"] = True
    if not update:
        return outcome
    return outcome.model_copy(update=update)
