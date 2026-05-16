"""Dispatch waiting status events to pipeline subscribers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from ralph.display.subscriber import PipelineSubscriber


def dispatch_waiting_event(
    event: object,
    *,
    subscriber: PipelineSubscriber | None,
    unit_id: str,
    agent_name: str,
) -> None:
    """Dispatch a WaitingStatusEvent to the subscriber.

    Exposed as a free function so tests can exercise it without a full pipeline.
    """
    if subscriber is not None:
        try:
            subscriber.record_waiting_status(event, unit_id=unit_id, agent_name=agent_name)
        except Exception:
            logger.debug("dispatch_waiting_event.record_waiting_status failed", exc_info=True)
