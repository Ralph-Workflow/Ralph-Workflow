from __future__ import annotations

import threading as _threading
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
    model_config = ConfigDict(frozen=True)

    action: AgentRetryAction | None = None
    session_id: str | None = None
    reset_tool_registry: bool = False
    failure_reason: str = ""
    skip_same_agent_retries: bool = False
    failed_agent_name: str | None = None
    broken_agent_reason: Literal["no_output", "prompt_echo", "no_llm_activity"] | None = None
    broken_agent_returncode: int | None = None
    broken_agent_stderr: str = ""

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


_CLEARED_AGENT_RETRY_INTENT: AgentRetryIntent = AgentRetryIntent()


def cleared_agent_retry_intent() -> AgentRetryIntent:
    return _CLEARED_AGENT_RETRY_INTENT


def agent_retry_intent_for_failure(
    *,
    failure_reason: str,
    session_id: str | None,
    reset_tool_registry: bool,
) -> AgentRetryIntent:

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


_retry_intent_local: _threading.local = _threading.local()


def set_last_captured_retry_intent(intent: AgentRetryIntent) -> None:
    _retry_intent_local.intent = intent
    if intent == cleared_agent_retry_intent():
        _retry_intent_local.fresh_bridge_requested = False


def fresh_bridge_requested() -> bool:
    raw: object = getattr(_retry_intent_local, "fresh_bridge_requested", False)
    return raw is True


def mark_fresh_bridge_requested() -> None:
    _retry_intent_local.fresh_bridge_requested = True


def clear_fresh_bridge_requested() -> None:
    _retry_intent_local.fresh_bridge_requested = False


def pop_last_captured_retry_intent() -> AgentRetryIntent:
    raw: object = getattr(_retry_intent_local, "intent", cleared_agent_retry_intent())
    intent = raw if isinstance(raw, AgentRetryIntent) else cleared_agent_retry_intent()
    _retry_intent_local.intent = cleared_agent_retry_intent()
    return intent


__all__ = [
    "AgentRetryAction",
    "AgentRetryIntent",
    "agent_retry_intent_for_failure",
    "clear_fresh_bridge_requested",
    "cleared_agent_retry_intent",
    "fresh_bridge_requested",
    "mark_fresh_bridge_requested",
    "pop_last_captured_retry_intent",
    "resume_agent_retry_intent",
    "set_last_captured_retry_intent",
]
