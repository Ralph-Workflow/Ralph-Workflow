"""The one seam every integration boundary uses to ask the single owner.

Ralph's integration lifecycle has exactly ONE decision owner for the
MUTATION ladder: :mod:`ralph.pipeline.integration_owner`. The owner
observes the live worktree, consults the durable attempt ledger and
projects the pure decision table onto the closed
:class:`~ralph.pipeline._integration_action.IntegrationAction`
vocabulary. Every other seam -- the runner's dispatch invariant, the
run loop's blocking seam, the diagnostic handoff -- asks the owner
through this module and acts on its verdict. No seam re-derives the
ladder from a durable record.

The split between the owner and the durable record facts
---------------------------------------------------------

The owner's projection can only ever produce the five mutation-ladder
actions:

* ``RESOLVE_ACTIVE_OPERATION``
* ``CONTINUE_GIT_OPERATION``
* ``REINTEGRATE_AGAINST_TARGET``
* ``LAND_TARGET``
* ``WAIT_FOR_BOUNDED_RETRY``

The remaining three members of the closed vocabulary are statements
about DURABLE STATE, which no live observation can express:

* ``REQUIRE_INTERVENTION`` -- the durable diagnostic-intervention flag
  on the integration record, plus the "a retained obligation with no
  record behind it" case. It is decided by
  :func:`ralph.pipeline._integration_record_facts.apply_record_facts`.
* ``PUBLISH_AFTER_LANDING`` -- the remote-publication obligation. It
  lives in the rebase state and is decided by
  :mod:`ralph.pipeline._publication_state_machine`; the durable
  integration record does not carry it.
* ``COMPLETE_WITH_PROOF`` -- the positive completion proof. It lives in
  the diagnostic path's four-condition Git proof
  (:mod:`ralph.pipeline._integration_diagnostic_proof`); the durable
  integration record does not carry it.

**The owner decides the mutation ladder; record-fact completion and
publication stay with the seam.** :func:`decide_seam_action` is that
seam: it asks the owner for the ladder and then overlays the durable
record facts through
:func:`ralph.pipeline._integration_record_facts.apply_record_facts`. A
seam that needs ``PUBLISH_AFTER_LANDING`` or ``COMPLETE_WITH_PROOF``
consults the driver that owns those facts -- it must never re-derive
them from a private decision table of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline._integration_record_facts import apply_record_facts
from ralph.pipeline.integration_owner import decide_integration

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline._integration_action import IntegrationAction
    from ralph.pipeline.auto_integrate_record import IntegrationRecord

__all__ = [
    "decide_seam_action",
]


def decide_seam_action(
    root: Path,
    *,
    record: IntegrationRecord | None = None,
    agent_active: bool = False,
    ledger_path: Path | None = None,
    retained: bool = False,
) -> IntegrationAction:
    """Ask the owner what integration does next, then overlay the record facts.

    Args:
        root: The workspace root the decision is made for. It anchors
            the durable attempt ledger when ``ledger_path`` is not
            given.
        record: The durable integration record the caller already
            holds (``None`` when the caller holds none). The owner
            reconciles it with the observed view: a caller holding a
            record the observation does not see holds a stale view, and
            the owner fails closed to the repair rung.
        agent_active: Whether an agent phase is active in the
            worktree. Recorded on the observation and rendered into
            the owner's reason.
        ledger_path: Override for the attempt-ledger file path.
        retained: True when the caller holds a RETAINED integration
            obligation. A retained obligation with no durable record
            behind it has nothing the boundary can act on, so the seam
            fails closed to ``REQUIRE_INTERVENTION``.

    Returns:
        The action the seam must act on: the owner's mutation-ladder
        answer, overlaid with the durable record facts.
    """
    owner_action, _owner_reason = decide_integration(
        root,
        record=record,
        agent_active=agent_active,
        ledger_path=ledger_path,
    )
    return apply_record_facts(owner_action, record, retained=retained)
