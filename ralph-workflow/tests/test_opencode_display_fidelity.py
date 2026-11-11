from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.display.preview_payload import payload_from_tool_event

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _parse(parser: OpenCodeParser, lines: Iterator[str]) -> list:
    return list(parser.parse(lines))


class TestOpenCodeDisplayFidelity:
    def test_read_tool_produces_file_preview_envelope(self) -> None:
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_use", "part": {"type": "tool", "tool": "Read",'
            ' "callID": "call_read", "state": {"status": "completed",'
            ' "input": {"path": "ralph/display/parallel_display.py"}}}}'
        )

        results = _parse(parser, _lines(line))

        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "Read"
        assert tool_uses[0].metadata["tool"] == "Read"
        assert tool_uses[0].metadata["input"]["path"] == "ralph/display/parallel_display.py"
        assert payload_from_tool_event("Read", tool_uses[0].metadata) is not None

    def test_write_tool_produces_syntax_preview_envelope(self) -> None:
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_use", "part": {"type": "tool", "tool": "write",'
            ' "callID": "call_write", "state": {"status": "completed",'
            ' "input": {"path": "x.py", "content": "value = 1\\n"}}}}'
        )

        results = _parse(parser, _lines(line))

        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "write"
        assert payload_from_tool_event("write", tool_uses[0].metadata) is not None

    def test_edit_tool_produces_diff_preview_envelope(self) -> None:
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_use", "part": {"type": "tool", "tool": "edit",'
            ' "callID": "call_edit", "state": {"status": "completed",'
            ' "input": {"path": "x.py", "oldText": "value = 1\\n",'
            ' "newText": "value = 2\\n"}}}}'
        )

        results = _parse(parser, _lines(line))

        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "edit"
        assert payload_from_tool_event("edit", tool_uses[0].metadata) is not None

    def test_running_then_completed_tool_does_not_duplicate(self) -> None:
        parser = OpenCodeParser()
        running = (
            '{"type":"tool_use","part":{"type":"tool","tool":"write",'
            '"callID":"call_dup","state":{"status":"running",'
            '"input":{"path":"x.py"}}}}'
        )
        completed = (
            '{"type":"tool_use","part":{"type":"tool","tool":"write",'
            '"callID":"call_dup","state":{"status":"completed",'
            '"input":{"path":"x.py","content":"value = 1\\n"},'
            '"output":"wrote 1 line"}}}'
        )

        results = _parse(parser, _lines(running, completed))

        assert [r.type for r in results] == ["tool_use", "tool_result"]

    def test_errored_tool_keeps_dispatch_visible(self) -> None:
        parser = OpenCodeParser()
        line = (
            '{"type": "tool_use", "sessionID": "ses_1", "part": {"type": "tool",'
            ' "tool": "task", "callID": "call_task", "state": {"status": "error",'
            ' "input": {"prompt": "x"}, "error": "MCP error -32001: Request timed out"}}}'
        )

        results = _parse(parser, _lines(line))

        assert [r.type for r in results] == ["tool_use", "error"]
        assert results[0].content == "task"
        assert results[1].content == "MCP error -32001: Request timed out"

    def test_every_non_lifecycle_event_in_opencode_wire_fixture_produces_a_parsed_line(
        self,
    ) -> None:
        from ralph.agents.parsers.opencode import OpenCodeParser as _Parser
        from tests.test_opencode_captured_wire import (
            _FIXTURE_PATH as _OPENCODE_WIRE_FIXTURE_PATH,
        )

        lines = _OPENCODE_WIRE_FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
        non_lifecycle_count = 0
        import json as _json

        for raw in lines:
            if not raw:
                continue
            obj = _json.loads(raw)
            event_type = obj.get("type")
            if event_type not in ("step_start", "step_finish"):
                non_lifecycle_count += 1
        parser = _Parser()
        parsed_lines = _parse(parser, iter(lines))
        assert len(parsed_lines) >= non_lifecycle_count, (
            f"Parser produced {len(parsed_lines)} AgentOutputLines for "
            f"{non_lifecycle_count} non-lifecycle input frames "
            f"({len(lines)} input frames total, of which "
            f"{len(lines) - non_lifecycle_count} are suppressed "
            f"lifecycle markers); at least one input frame was dropped"
        )


@pytest.mark.parametrize(
    ("scenario", "frames"),
    [
        (
            "write_then_read",
            [
                '{"type":"tool_use","part":{"type":"tool","tool":"write",'
                '"callID":"c1","state":{"status":"completed",'
                '"input":{"path":"a.py","content":"x = 1\\n"},"output":"wrote"}}}',
                '{"type":"tool_use","part":{"type":"tool","tool":"read",'
                '"callID":"c2","state":{"status":"completed",'
                '"input":{"path":"a.py"},"output":"x = 1\\n"}}}',
            ],
        ),
        (
            "write_then_edit",
            [
                '{"type":"tool_use","part":{"type":"tool","tool":"write",'
                '"callID":"c1","state":{"status":"completed",'
                '"input":{"path":"a.py","content":"x = 1\\n"},"output":"wrote"}}}',
                '{"type":"tool_use","part":{"type":"tool","tool":"edit",'
                '"callID":"c2","state":{"status":"completed",'
                '"input":{"path":"a.py","oldText":"x = 1\\n",'
                '"newText":"x = 2\\n"},"output":"edited"}}}',
            ],
        ),
    ],
)
def test_opencode_capability_routes_produce_recognized_envelopes(
    scenario: str, frames: list[str]
) -> None:
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(frames))
    assert len(parsed) >= len(frames)
    for line in parsed:
        if line.type != "tool_use":
            continue
        meta = line.metadata or {}
        assert "tool" in meta, (
            f"Scenario {scenario!r}: parsed line {line.type!r} "
            f"has no tool metadata; payload_from_tool_event would None"
        )
        assert payload_from_tool_event(line.content, meta) is not None, (
            f"Scenario {scenario!r}: payload_from_tool_event returned "
            f"None for tool {line.content!r}; the display layer would "
            f"silently render nothing"
        )


def test_supported_capability_without_render_breaks_via_recorder() -> None:
    from ralph.agents.display_capabilities import DisplayCapability
    from ralph.agents.display_capability_stance import DisplayCapabilityStance
    from ralph.display.capability_observation_recorder import CapabilityObservationRecorder

    recorder = CapabilityObservationRecorder()
    parser = OpenCodeParser()
    line = (
        '{"type":"tool_use","part":{"type":"tool","tool":"Read",'
        '"callID":"c1","state":{"status":"completed",'
        '"input":{"path":"x.py"},"output":"contents"}}}'
    )
    parsed = _parse(parser, _lines(line))
    assert len(parsed) >= 1
    assert any(r.type == "tool_use" and r.content == "Read" for r in parsed)

    assert recorder.observed_capabilities() == frozenset()

    supported = DisplayCapabilityStance.supported(DisplayCapability.FILE_PREVIEW)
    assert supported.is_supported
    assert DisplayCapability.FILE_PREVIEW not in recorder.observed_capabilities()


def test_opencode_wire_fixture_is_loadable_json() -> None:
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    for line_number, raw in enumerate(
        _FIXTURE_PATH.read_text(encoding="utf-8").splitlines(), start=1
    ):
        json.loads(raw)
        assert line_number >= 1


def test_captured_fixture_drives_all_three_display_surfaces() -> None:
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    fixture_lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(fixture_lines))

    operations_seen: set[str] = set()
    for line in parsed:
        if line.type != "tool_use":
            continue
        meta = line.metadata or {}
        assert "tool" in meta, f"Tool envelope missing canonical name for {line.content!r}"
        payload = payload_from_tool_event(line.content, meta)
        assert payload is not None, (
            f"payload_from_tool_event returned None for "
            f"{line.content!r}; the captured fixture must produce a "
            f"recognized envelope for every tool_use frame"
        )
        operations_seen.add(payload.operation)

    assert "read" in operations_seen, (
        f"Captured fixture missing read operation; saw {sorted(operations_seen)}"
    )
    assert "write" in operations_seen, (
        f"Captured fixture missing write operation; saw {sorted(operations_seen)}"
    )
    assert "replace" in operations_seen, (
        f"Captured fixture missing edit (replace) operation; saw {sorted(operations_seen)}"
    )


def test_captured_fixture_write_envelope_has_syntax_content() -> None:
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    fixture_lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(fixture_lines))

    write_tool_uses = [
        line for line in parsed if line.type == "tool_use" and line.content == "write"
    ]
    assert len(write_tool_uses) == 1, (
        f"Captured fixture should produce exactly one write tool_use, got {len(write_tool_uses)}"
    )
    write_meta = write_tool_uses[0].metadata or {}
    payload = payload_from_tool_event("write", write_meta)
    assert payload is not None
    assert payload.operation == "write"
    assert payload.content, (
        "Write payload content is empty; the captured fixture's "
        "write envelope is missing the file body the syntax "
        "preview is supposed to render"
    )
    assert "function TodoList" in (payload.content or ""), (
        "Write payload content does not include the captured file "
        "body; the captured write envelope must carry the actual "
        "file content for the syntax preview to render"
    )


def test_captured_fixture_edit_envelope_has_diff_hunks() -> None:
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    fixture_lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(fixture_lines))

    edit_tool_uses = [line for line in parsed if line.type == "tool_use" and line.content == "edit"]
    assert len(edit_tool_uses) == 1, (
        f"Captured fixture should produce exactly one edit tool_use, got {len(edit_tool_uses)}"
    )
    edit_meta = edit_tool_uses[0].metadata or {}
    payload = payload_from_tool_event("edit", edit_meta)
    assert payload is not None
    assert payload.operation == "replace"
    assert len(payload.hunks) == 1, (
        f"Edit payload must surface exactly one diff hunk for the "
        f"captured 1.18.14 edit frame, got {len(payload.hunks)}"
    )
    hunk = payload.hunks[0]
    assert "this.items = [];" in hunk.old_text, (
        f"Edit hunk old_text missing captured old string; got {hunk.old_text!r}"
    )
    assert "this.nextId = 0;" in hunk.new_text, (
        f"Edit hunk new_text missing captured new string; got {hunk.new_text!r}"
    )


def test_captured_fixture_read_envelope_has_path() -> None:
    from tests.test_opencode_captured_wire import _FIXTURE_PATH

    fixture_lines = _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()
    parser = OpenCodeParser()
    parsed = _parse(parser, iter(fixture_lines))

    read_tool_uses = [line for line in parsed if line.type == "tool_use" and line.content == "read"]
    assert len(read_tool_uses) == 2, (
        f"Captured fixture should produce 2 read tool_uses (one "
        f"errored initial read + one successful read-back), got "
        f"{len(read_tool_uses)}"
    )
    for line in read_tool_uses:
        meta = line.metadata or {}
        payload = payload_from_tool_event("read", meta)
        assert payload is not None, (
            f"Read payload is None for {meta!r}; the captured "
            f"read envelope must produce a recognized file_preview"
        )
        assert payload.operation == "read"
        assert payload.path, (
            "Read payload path is empty; the captured read "
            "envelope must carry the filePath argument so the "
            "file preview can render the path"
        )
        assert payload.path.endswith("todo-list.js"), (
            f"Read payload path is {payload.path!r}, expected to "
            f"end with 'todo-list.js' (the captured file path)"
        )
