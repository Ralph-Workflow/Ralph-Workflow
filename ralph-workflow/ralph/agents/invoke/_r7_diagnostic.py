"""Structured R7 diagnostic for the ``subagents/`` layout-absent probe.

Lives in its own module so the audit_repo_structure ``one
top-level class per file`` policy is satisfied. The diagnostic is
imported by ``_subagent_transcript.py`` (the tailer) and the test
suite; the separation keeps both call sites independent of the
tailer's private surface.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class R7AbsentLayoutDiagnostic:
    """Structured R7 diagnostic emitted when the ``subagents/`` layout is absent.

    Fields:
        code: Always ``"R7_SUBAGENT_LAYOUT_MISSING"``; the constant string
            a downstream consumer keys off.
        claude_code_version: The Claude Code version recorded on the
            parent's user/assistant record (``obj.version``); may be
            ``None`` when the parent record lacks the field.
        project_key: The project-key path component (workspace absolute
            path with ``/`` replaced by ``-``).
        session_id: The captured parent session id.
        probed_path: The absolute path that was probed at dispatch time.
        dispatch_tool_use_id: The ``tool_use_id`` of the dispatch that
            triggered the probe.
        dispatch_tool_name: Either ``"Agent"`` or ``"Task"`` (the
            subagent-dispatch set is the canonical two-element set).
    """

    code: str
    claude_code_version: str | None
    project_key: str
    session_id: str
    probed_path: str
    dispatch_tool_use_id: str
    dispatch_tool_name: str


__all__ = ["R7AbsentLayoutDiagnostic"]
