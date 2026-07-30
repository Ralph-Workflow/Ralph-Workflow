"""Generic execution strategy for agents."""

from __future__ import annotations

from ._base import BaseExecutionStrategy


class GenericExecutionStrategy(BaseExecutionStrategy):
    """Default strategy: single-process lifetime, exit 0 is terminal success.

    Used only by transports whose registered contract does not opt into
    session continuation or durable completion enforcement.
    """
