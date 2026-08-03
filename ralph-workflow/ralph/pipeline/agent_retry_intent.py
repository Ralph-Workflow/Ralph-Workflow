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
    skip_same_agent_retries: bool = False

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


# wt-05-test-opti: cache the empty intent. ``AgentRetryIntent()`` is a
# frozen Pydantic model with all-default fields; constructing a fresh
# instance on every ``cleared_agent_retry_intent()`` call costs ~50 us
# of Pydantic validation overhead, and the function is called from the
# conftest autouse fixture for every test (~12.8k times per ``make
# test``), in the pipeline reducer at least three times per state
# transition, and from the effect executor every time the captured
# intent is read. A single module-level singleton short-circuits the
# constructor + validator and is safe because the model is frozen and
# the function is documented as "the empty intent used to clear
# next-attempt session state" \u2014 mutability is impossible and call
# sites only read fields.
_CLEARED_AGENT_RETRY_INTENT: AgentRetryIntent = AgentRetryIntent()


def cleared_agent_retry_intent() -> AgentRetryIntent:
    """Return the empty intent used to clear next-attempt session state."""
    return _CLEARED_AGENT_RETRY_INTENT


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
    """Build a resume intent that reuses an existing agent session id."""
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
