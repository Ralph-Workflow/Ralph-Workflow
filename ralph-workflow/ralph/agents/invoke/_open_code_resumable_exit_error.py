from __future__ import annotations

from ralph.agents.invoke._errors import AgentInvocationError


class OpenCodeResumableExitError(AgentInvocationError):
    def __init__(
        self,
        agent_name: str,
        session_id: str | None = None,
        *,
        last_observed_tool_call: str | None = None,
        last_evidence_summary: str | None = None,
        elapsed_seconds: float | None = None,
        transcript_tail: tuple[str, ...] = (),
    ) -> None:
        self.resumable_session_id = session_id
        self.last_observed_tool_call = last_observed_tool_call
        self.last_evidence_summary = last_evidence_summary
        self.elapsed_seconds = elapsed_seconds
        self.transcript_tail = transcript_tail
        base_message = (
            "agent session exited without required completion evidence "
            "(completion sentinel missing, or required artifact receipt missing)"
        )
        diagnostic_suffix_parts: list[str] = []
        if last_observed_tool_call is not None:
            diagnostic_suffix_parts.append(f"last_tool_call={last_observed_tool_call}")
        if elapsed_seconds is not None:
            diagnostic_suffix_parts.append(f"elapsed={round(elapsed_seconds, 1)}s")
        if diagnostic_suffix_parts:
            full_message = base_message + " [" + ", ".join(diagnostic_suffix_parts) + "]"
        else:
            full_message = base_message
        super().__init__(agent_name, 0, full_message)


__all__ = ["OpenCodeResumableExitError"]
