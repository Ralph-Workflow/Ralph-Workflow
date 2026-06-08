"""Canonical retry/session intent for the next agent attempt."""

from __future__ import annotations

from importlib import import_module
from typing import Literal, Protocol, cast

from pydantic import ConfigDict, model_validator

from ralph.pydantic_compat import RalphBaseModel

AgentRetryAction = Literal["fresh", "resume", "new_session_with_id"]


class _RecoveryActionForFailureReason(Protocol):
    def __call__(
        self,
        failure_reason: str,
        *,
        has_prior_session: bool,
        reset_tool_registry: bool = False,
    ) -> AgentRetryAction: ...


class AgentRetryIntent(RalphBaseModel):
    """Single source of truth for the next-attempt session action."""

    model_config = ConfigDict(frozen=True)

    action: AgentRetryAction | None = None
    session_id: str | None = None
    reset_tool_registry: bool = False
    failure_reason: str = ""

    @model_validator(mode="after")
    def _validate_action_session_pair(self) -> AgentRetryIntent:
        if self.action in {"resume", "new_session_with_id"} and not self.session_id:
            raise RuntimeError(
                "AgentRetryIntent action requires session_id; "
                f"got action={self.action!r} session_id={self.session_id!r}"
            )
        if self.action is None and self.session_id is not None:
            raise RuntimeError(
                "AgentRetryIntent with action=None must not carry session_id; "
                f"got {self.session_id!r}"
            )
        return self


def cleared_agent_retry_intent() -> AgentRetryIntent:
    return AgentRetryIntent()


def agent_retry_intent_for_failure(
    *,
    failure_reason: str,
    session_id: str | None,
    reset_tool_registry: bool,
) -> AgentRetryIntent:
    """Build the canonical next-attempt action from failure semantics."""

    module = import_module("ralph.agents.invoke._session_resume")
    recovery_action_for_failure_reason = cast(
        "_RecoveryActionForFailureReason",
        module.recovery_action_for_failure_reason,
    )

    action = recovery_action_for_failure_reason(
        failure_reason,
        has_prior_session=bool(session_id),
        reset_tool_registry=reset_tool_registry,
    )
    if action == "fresh":
        return AgentRetryIntent(
            action="fresh",
            session_id=None,
            reset_tool_registry=False,
            failure_reason=failure_reason,
        )
    return AgentRetryIntent(
        action=action,
        session_id=session_id,
        reset_tool_registry=reset_tool_registry,
        failure_reason=failure_reason,
    )


def resume_agent_retry_intent(
    session_id: str,
    *,
    failure_reason: str = "",
    reset_tool_registry: bool = False,
) -> AgentRetryIntent:
    return AgentRetryIntent(
        action="resume",
        session_id=session_id,
        reset_tool_registry=reset_tool_registry,
        failure_reason=failure_reason,
    )


__all__ = [
    "AgentRetryAction",
    "AgentRetryIntent",
    "agent_retry_intent_for_failure",
    "cleared_agent_retry_intent",
    "resume_agent_retry_intent",
]
