from __future__ import annotations

from ralph.agents.invoke._agent_invocation_error import AgentInvocationError
from ralph.recovery.typed_signal import TypedSignal


class MissingCredentialsError(AgentInvocationError):
    typed_signal_provider_originated: bool = True

    def __init__(self, agent_name: str, detail: str) -> None:
        self.skip_same_agent_retries = True
        self.env_var = detail.split(maxsplit=1)[0]
        super().__init__(agent_name, 1, detail)

    @property
    def typed_signal(self) -> TypedSignal:
        return TypedSignal(
            name=type(self).__name__,
            provider_originated=type(self).typed_signal_provider_originated,
        )


__all__ = ["MissingCredentialsError"]
