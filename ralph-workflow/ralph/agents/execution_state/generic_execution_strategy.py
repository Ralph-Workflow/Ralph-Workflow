"""Generic execution strategy for agents."""

from __future__ import annotations

from ._base import BaseExecutionStrategy


class GenericExecutionStrategy(BaseExecutionStrategy):
    """Default strategy: single-process lifetime, exit 0 is terminal success.

    Replicates the behaviour that existed before the session-aware model was
    introduced so that Claude/Codex paths are unaffected.
    """
