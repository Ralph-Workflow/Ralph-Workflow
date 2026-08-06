"""Deduplicate a leading tool-name token on a live tool-result body.

Split out of :mod:`ralph.display.agent_event_renderer` so that module's
line count stays under the repo-structure file-size floor (the same reason
:mod:`ralph.display._edit_preview_render` was split out of
:mod:`ralph.display.edit_preview`).
"""

from __future__ import annotations

__all__ = ["strip_duplicate_tool_prefix"]


def strip_duplicate_tool_prefix(body: str, tool_name: str) -> str:
    """Remove a leading duplicate of ``tool_name`` from a result body (B1).

    Parser-synthesized result summaries (e.g. ``AgyParser._completion_summary``,
    used whenever a tool's DONE frame carries no ``tool_info.output``) build
    their content starting with the tool's own label, e.g.
    ``"write_to_file todo-list.js (0.08s)"``. The live-activity renderer
    separately renders the tool name as its own segment before the body, so
    passing the body through unchanged doubles the tool name on the live
    activity line: ``write_to_file write_to_file todo-list.js (0.08s)``.
    Strip one leading occurrence (case-insensitive) so the tool name appears
    exactly once. A body that does not start with the tool name (e.g.
    ``view_file``'s ``"19 lines, 1395 bytes"``, which carries a real
    ``tool_info.output``) is returned unchanged.
    """
    if not tool_name or not body:
        return body
    prefix = f"{tool_name} "
    if body.casefold().startswith(prefix.casefold()):
        return body[len(prefix) :]
    if body.casefold() == tool_name.casefold():
        return ""
    return body
