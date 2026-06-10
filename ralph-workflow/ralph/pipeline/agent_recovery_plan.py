"""Resolved retry plan for a failed agent invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.agent_retry_intent import AgentRetryAction


@dataclass(frozen=True)
class AgentRecoveryPlan:
    """Resolved retry plan for a failed agent invocation.

    The ``recovery_action`` field is APPENDED (not inserted) so existing
    positional construction of this dataclass keeps working unchanged.
    The field is ``None`` for un-updated call sites; the single owner of
    the field is ``build_agent_recovery_plan`` in
    ``ralph/pipeline/effect_executor.py``, and the only consumer is the
    retry-prompt constructor in the same module.
    """

    prompt_file: str
    session_id: str | None
    reason: str
    recovery_action: AgentRetryAction | None = None
