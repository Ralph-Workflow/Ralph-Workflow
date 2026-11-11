
from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class RunMetrics(RalphBaseModel):

    model_config = _FROZEN

    total_agent_calls: int = 0
    total_continuations: int = 0
    total_fallbacks: int = 0
    total_retries: int = 0

    def with_retry_increment(self) -> RunMetrics:
        return RunMetrics(
            total_agent_calls=self.total_agent_calls,
            total_continuations=self.total_continuations,
            total_fallbacks=self.total_fallbacks,
            total_retries=self.total_retries + 1,
        )

    def with_fallback_increment(self) -> RunMetrics:
        return RunMetrics(
            total_agent_calls=self.total_agent_calls,
            total_continuations=self.total_continuations,
            total_fallbacks=self.total_fallbacks + 1,
            total_retries=self.total_retries,
        )

    def with_continuation_increment(self) -> RunMetrics:
        return RunMetrics(
            total_agent_calls=self.total_agent_calls,
            total_continuations=self.total_continuations + 1,
            total_fallbacks=self.total_fallbacks,
            total_retries=self.total_retries,
        )
