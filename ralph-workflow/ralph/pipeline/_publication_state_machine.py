from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.rebase_state import RebaseState


class PublicationDecision(StrEnum):
    PUBLICATION_PENDING = "publication_pending"
    PUBLISH_NOW = "publish_now"
    PUBLISH_DEFERRED_FRESHNESS = "publish_deferred_freshness"
    PUBLISH_DEFERRED_REMOTE = "publish_deferred_remote"
    PUBLICATION_TERMINAL = "publication_terminal"
    PUBLICATION_BLOCKED = "publication_blocked"


def _freshness_publication_action(freshness: str | None) -> PublicationDecision:
    if freshness == "verified":
        return PublicationDecision.PUBLISH_NOW
    if freshness == "unsafe":
        return PublicationDecision.PUBLICATION_BLOCKED
    if freshness in {"degraded", "unverified"}:
        return PublicationDecision.PUBLISH_DEFERRED_FRESHNESS
    return PublicationDecision.PUBLISH_DEFERRED_FRESHNESS


def next_publication_action(
    state: RebaseState,
    *,
    record: IntegrationRecord | None,
    remote_unavailable: bool,
    configured_remote: str | None,
) -> PublicationDecision:
    if configured_remote is None:
        return PublicationDecision.PUBLICATION_TERMINAL
    if record is not None and record.phase != "integrated":
        return PublicationDecision.PUBLICATION_PENDING
    if not state.fast_forwarded:
        return PublicationDecision.PUBLICATION_PENDING
    if remote_unavailable:
        return PublicationDecision.PUBLISH_DEFERRED_REMOTE
    return _freshness_publication_action(state.freshness_verdict)


__all__ = [
    "PublicationDecision",
    "next_publication_action",
]
