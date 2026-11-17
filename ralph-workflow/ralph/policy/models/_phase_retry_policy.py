from __future__ import annotations

from pydantic import Field

from ralph.pydantic_compat import RalphBaseModel


class PhaseRetryPolicy(RalphBaseModel):
    max_retries: int = Field(default=3, ge=0)
    retry_delay_ms: int = Field(default=1000, ge=0)
    retry_in_session: bool = False
