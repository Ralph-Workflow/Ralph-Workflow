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
    # Stale-session framing metadata. APPENDED at the end (not reordered) so
    # existing positional construction of this dataclass keeps working
    # unchanged. All new fields default to ``None`` so un-updated call sites
    # stay valid. Threaded from ``AgentRecoveryInput`` by
    # ``build_agent_recovery_plan`` and consumed by
    # ``_write_agent_retry_prompt`` to produce the structured
    # ``STALE SESSION RECOVERY`` block in fresh-mode retry prompts.
    stale_session_id: str | None = None
    transport: str | None = None
    model: str | None = None
