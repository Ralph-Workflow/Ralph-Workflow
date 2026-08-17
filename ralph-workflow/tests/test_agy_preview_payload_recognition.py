"""Regression: AGY content-edit tools reach a syntax-highlighted preview.

AGY's parser emits ``metadata = {"tool": ..., "tool_info": {"name": ...,
"parameters": {...}}}`` (see ``ralph/agents/parsers/agy.py``). The preview
pipeline must recognize this nested envelope, AGY's content-edit tool names
(``write_to_file`` / ``replace_file_content`` / ``multi_replace_file_content``
/ ``sed_file`` / ``notebook_edit``), and the ``TargetFile`` parameter key so a
syntax-highlighted preview fires at the RENDER layer -- not just at any single
payload layer.

Each tool is driven through both :func:`payload_from_tool_event` (the payload
layers: ``_input`` tool_info unwrap, the operations / dispatch map, and the
``_path`` ``TargetFile`` key) and :func:`build_edit_preview` (the render layer,
fed the raw metadata dict so the display preview-wrapping path is exercised
too). A regression in any layer breaks the render assertion.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Group
from rich.syntax import Syntax

from ralph.agents.parsers.agy import AgyParser
from ralph.display.edit_preview import build_edit_preview
from ralph.display.preview_payload import payload_from_tool_event


def _agy_metadata(
    tool: str,
    *,
    target: str = "/workspace/x.py",
    **parameters: object,
) -> dict[str, object]:
    """Build the parser-real AGY envelope (nested ``tool_info.parameters``)."""
    return {
        "tool": tool,
        "tool_info": {"name": tool, "parameters": {"TargetFile": target, **parameters}},
    }


def _render_contains_syntax(renderable: Any) -> bool:
    if isinstance(renderable, Syntax):
        return True
    if isinstance(renderable, Group):
        return any(_render_contains_syntax(child) for child in renderable.renderables)
    return False


def test_write_to_file_drives_write_payload_and_syntax_render() -> None:
    metadata = _agy_metadata("write_to_file", content="print('hi')\n")
    payload = payload_from_tool_event("write_to_file", metadata)
    assert payload is not None
    assert payload.operation == "write"
    assert payload.path == "/workspace/x.py"
    assert payload.content == "print('hi')\n"
    renderable = build_edit_preview(
        "write_to_file", metadata, width=80, terminal_bg_is_light=None
    )
    assert isinstance(renderable, Syntax), type(renderable)


def test_replace_file_content_drives_replace_payload_and_group_render() -> None:
    metadata = _agy_metadata(
        "replace_file_content", old_string="old", new_string="new"
    )
    payload = payload_from_tool_event("replace_file_content", metadata)
    assert payload is not None
    assert payload.operation == "replace"
    assert payload.path == "/workspace/x.py"
    assert len(payload.hunks) == 1
    assert payload.hunks[0].old_text == "old"
    assert payload.hunks[0].new_text == "new"
    renderable = build_edit_preview(
        "replace_file_content", metadata, width=80, terminal_bg_is_light=None
    )
    assert isinstance(renderable, Group), type(renderable)
    assert _render_contains_syntax(renderable)


def test_multi_replace_file_content_drives_replace_payload_and_group_render() -> None:
    metadata = _agy_metadata(
        "multi_replace_file_content",
        edits=[{"old_string": "a", "new_string": "b"}],
    )
    payload = payload_from_tool_event("multi_replace_file_content", metadata)
    assert payload is not None
    assert payload.operation == "replace"
    assert payload.path == "/workspace/x.py"
    assert len(payload.hunks) == 1
    renderable = build_edit_preview(
        "multi_replace_file_content", metadata, width=80, terminal_bg_is_light=None
    )
    assert isinstance(renderable, Group), type(renderable)
    assert _render_contains_syntax(renderable)


def test_sed_file_drives_replace_payload_and_group_render() -> None:
    metadata = _agy_metadata("sed_file", old_string="1", new_string="2")
    payload = payload_from_tool_event("sed_file", metadata)
    assert payload is not None
    assert payload.operation == "replace"
    assert payload.path == "/workspace/x.py"
    assert len(payload.hunks) == 1
    renderable = build_edit_preview(
        "sed_file", metadata, width=80, terminal_bg_is_light=None
    )
    assert isinstance(renderable, Group), type(renderable)
    assert _render_contains_syntax(renderable)


def test_notebook_edit_drives_write_payload_and_syntax_render() -> None:
    metadata = _agy_metadata(
        "notebook_edit", target="/workspace/nb.ipynb", new_source="print(1)\n"
    )
    payload = payload_from_tool_event("notebook_edit", metadata)
    assert payload is not None
    assert payload.operation == "write"
    assert payload.path == "/workspace/nb.ipynb"
    renderable = build_edit_preview(
        "notebook_edit", metadata, width=80, terminal_bg_is_light=None
    )
    assert isinstance(renderable, Syntax), type(renderable)


def test_tool_done_frame_keeps_canonical_payload_end_to_end() -> None:
    """Plan S-6: a wire-shaped ``step_type=tool`` DONE frame survives the
    ``_dispatch_tool_update`` path with the canonical payload envelope.

    The frame mirrors the measured v1.1.13 wire shape (see
    ``tests/display/_fixtures/agy_wire_v1_1_13.jsonl``): a ``step_update``
    with ``step_type=tool``, ``state=DONE``, ``tool_name``, and a
    ``tool_info`` carrying ``name`` / ``parameters`` / ``output``. The
    metadata the parser emits must still unwrap through
    :func:`payload_from_tool_event` into the canonical payload shape.
    """
    frame = {
        "event": "step_update",
        "step_update": {
            "conversation_id": "synthetic",
            "step_index": 7,
            "state": "DONE",
            "step_type": "tool",
            "tool_name": "write_to_file",
            "duration_seconds": 0.01,
            "tool_info": {
                "name": "write_to_file",
                "parameters": {
                    "TargetFile": "/workspace/x.py",
                    "content": "print('hi')\n",
                },
                "output": "Wrote 1 line to /workspace/x.py",
            },
        },
    }
    events = list(AgyParser().parse(iter([json.dumps(frame)])))
    tool_results = [event for event in events if event.type == "tool_result"]
    assert len(tool_results) == 1, f"expected one tool_result, got {[e.type for e in events]}"
    metadata = tool_results[0].metadata
    assert metadata is not None
    assert metadata["tool"] == "write_to_file"
    payload = payload_from_tool_event("write_to_file", metadata)
    assert payload is not None
    assert payload.operation == "write"
    assert payload.path == "/workspace/x.py"
    assert payload.content == "print('hi')\n"
