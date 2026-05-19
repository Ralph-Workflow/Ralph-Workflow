"""Reference to a prior session when a development result is partial."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class Continuation(RalphBaseModel):
    """Reference to a prior session when a development result is partial."""

    model_config = ConfigDict(extra="forbid")

    prior_session_id: str = Field(..., min_length=1)
