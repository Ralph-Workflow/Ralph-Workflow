"""Fallover record model for pipeline state."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class FalloverRecord(RalphBaseModel):
    """A record of a single agent fallover event persisted in pipeline state."""

    model_config = _FROZEN

    phase: str
    from_agent: str
    to_agent: str
    timestamp_iso: str
