"""Preserve durable unresolved integration evidence at optional integration seams.

Preserving that evidence and REFUSING TO RETRY are two different things,
and conflating them deadlocks the run. The integration seam is not
ordinary work that must wait for a conflict to be resolved -- it is the
thing that resolves it. Gating it on the same verdict that gates
ordinary phase dispatch leaves the only operation able to clear the
verdict blocked by the verdict, so ``last_action == "conflict"`` latches
for the rest of the run and
:func:`~ralph.pipeline.integration_resolution.assert_non_resolution_dispatch_allowed`
then rejects every phase -- against a repository that may already be
clean.

The split this module encodes:

* ORDINARY dispatch stays fail-closed on the verdict. Untouched.
* The integration seam always runs, and its OUTCOME is filtered through
  :func:`preserve_unresolved_resolution_state`, so a seam that did not
  land cannot launder a recorded conflict into a "skip". Only a real
  landing clears the evidence -- which is the invariant the early return
  was reaching for, without the deadlock it brought with it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.integration_resolution import persisted_integration_resolution_verdict

if TYPE_CHECKING:
    from ralph.pipeline.rebase_state import RebaseState

__all__ = [
    "preserve_unresolved_resolution_state",
    "retains_unresolved_resolution_state",
]


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
      the outcome stands as written, and the latch is released;
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
        return outcome
    if not retains_unresolved_resolution_state(prior):
        return outcome

    update: dict[str, object] = {}
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
