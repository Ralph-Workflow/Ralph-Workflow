from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.display.preview_payload import payload_from_tool_event

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _parse(parser: OpenCodeParser, lines: Iterator[str]) -> list:
    return list(parser.parse(lines))


_FIXTURE_PATH: Path = Path(__file__).parent / "display" / "_fixtures" / "opencode_wire.jsonl"


class TestOpenCodeCapturedWire:
    def test_captured_ralph_read_file_routes_to_file_preview(self) -> None:
        parser = OpenCodeParser()
        line = (
            '{"type":"tool_use","timestamp":1785000000000,'
            '"sessionID":"ses_00000000000000000000",'
            '"part":{"type":"tool","tool":"ralph_read_file",'
            '"callID":"call_0000000000000000",'
            '"state":{"status":"completed",'
            '"input":{"path":"/tmp/normalized/todo-list.js"},'
            '"output":"x = 1\\n"}'
            ',"id":"prt_00000000000000000000",'
            '"sessionID":"ses_00000000000000000000",'
            '"messageID":"msg_00000000000000000000"}}'
        )
        results = _parse(parser, _lines(line))
        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "read_file"
        assert tool_uses[0].metadata["tool"] == "read_file"
        assert tool_uses[0].metadata["tool_raw"] == "ralph_read_file"
        payload = payload_from_tool_event("read_file", tool_uses[0].metadata)
        assert payload is not None
        assert payload.operation == "read"

    def test_captured_ralph_write_file_routes_to_syntax_preview(self) -> None:
        parser = OpenCodeParser()
        line = (
            '{"type":"tool_use","timestamp":1785000000000,'
            '"sessionID":"ses_00000000000000000000",'
            '"part":{"type":"tool","tool":"ralph_write_file",'
            '"callID":"call_0000000000000000",'
            '"state":{"status":"completed",'
            '"input":{"path":"/tmp/normalized/todo-list.js",'
            '"content":"function TodoList() {}\\n"},'
            '"output":"wrote 1 line"}'
            ',"id":"prt_00000000000000000000",'
            '"sessionID":"ses_00000000000000000000",'
            '"messageID":"msg_00000000000000000000"}}'
        )
        results = _parse(parser, _lines(line))
        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "write_file"
        assert tool_uses[0].metadata["tool"] == "write_file"
        assert tool_uses[0].metadata["tool_raw"] == "ralph_write_file"
        payload = payload_from_tool_event("write_file", tool_uses[0].metadata)
        assert payload is not None
        assert payload.operation == "write"
        assert payload.content == "function TodoList() {}\n"

    def test_captured_ralph_edit_file_routes_to_diff_preview(self) -> None:
        parser = OpenCodeParser()
        line = (
            '{"type":"tool_use","timestamp":1785000001000,'
            '"sessionID":"ses_00000000000000000000",'
            '"part":{"type":"tool","tool":"ralph_edit_file",'
            '"callID":"call_0000000000000001",'
            '"state":{"status":"completed",'
            '"input":{"path":"/tmp/normalized/todo-list.js",'
            '"oldText":"function TodoList() {}\\n",'
            '"newText":"function TodoList() { this.items = []; }\\n"},'
            '"output":"edited 1 location"}'
            ',"id":"prt_00000000000000000001",'
            '"sessionID":"ses_00000000000000000000",'
            '"messageID":"msg_00000000000000000000"}}'
        )
        results = _parse(parser, _lines(line))
        tool_uses = [r for r in results if r.type == "tool_use"]
        assert len(tool_uses) == 1
        assert tool_uses[0].content == "edit_file"
        assert tool_uses[0].metadata["tool"] == "edit_file"
        assert tool_uses[0].metadata["tool_raw"] == "ralph_edit_file"
        payload = payload_from_tool_event("edit_file", tool_uses[0].metadata)
        assert payload is not None
        assert payload.operation == "replace"
        assert len(payload.hunks) == 1
        assert payload.hunks[0].old_text == "function TodoList() {}\n"
        assert payload.hunks[0].new_text == ("function TodoList() { this.items = []; }\n")

    def test_captured_real_fixture_routes_every_tool_use(self) -> None:
        fixture = _FIXTURE_PATH
        fixture_lines = fixture.read_text(encoding="utf-8").splitlines()
        parser = OpenCodeParser()
        parsed = _parse(parser, iter(fixture_lines))
        assert len(parsed) >= 9, (
            f"Parser dropped frames: got {len(parsed)} AgentOutputLine "
            f"items from {len(fixture_lines)} input frames"
        )
        tool_uses = [r for r in parsed if r.type == "tool_use"]
        tool_results = [r for r in parsed if r.type == "tool_result"]
        errors = [r for r in parsed if r.type == "error"]
        assert len(tool_uses) == 4, (
            f"Expected 4 tool_use lines (read/write/edit/read), got {len(tool_uses)}"
        )
        assert len(tool_results) == 3, (
            f"Expected 3 tool_result lines (write/edit/read), got {len(tool_results)}"
        )
        assert len(errors) >= 1, (
            f"Expected at least 1 error line (the failed read attempt), got {len(errors)}"
        )
        seen_tools: set[str] = set()
        for line in tool_uses:
            meta = line.metadata or {}
            assert "tool" in meta
            assert "input" in meta, (
                f"Parser did not record an input envelope for {line.content!r}; "
                f"the display layer would have no arguments to preview"
            )
            seen_tools.add(line.content)
        assert {"read", "write", "edit"} <= seen_tools, (
            f"Captured fixture did not exercise all three display surfaces: "
            f"missing one of read/write/edit. saw={sorted(seen_tools)}"
        )

    def test_captured_ralph_envelope_normalizes_at_transport_boundary(self) -> None:
        lines = _RAILPH_OPENCODE_ROUTER_LAYER_FIXTURE_LINES
        parser = OpenCodeParser()
        parsed = _parse(parser, iter(lines))
        tool_uses = [r for r in parsed if r.type == "tool_use"]
        assert len(tool_uses) == 4
        assert {line.metadata["tool"] for line in tool_uses} == {
            "read_file",
            "write_file",
            "edit_file",
        }
        for line in tool_uses:
            assert line.metadata["tool_raw"].startswith("ralph_")
        seen_operations: set[str] = set()
        for line in tool_uses:
            payload = payload_from_tool_event(line.content, line.metadata)
            assert payload is not None
            seen_operations.add(payload.operation)
        assert {"read", "write", "replace"} <= seen_operations


_RAILPH_OPENCODE_ROUTER_LAYER_FIXTURE_LINES: tuple[str, ...] = (
    '{"type":"step_start","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"id":"prt_00000000000000000000","messageID":"msg_00000000000000000000","sessionID":"ses_00000000000000000000","type":"step-start"}}',
    '{"type":"tool_use","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_read_file","callID":"call_0000000000000003","state":{"status":"completed","input":{"path":"/workspace/normalized"},"output":"x = 1\\n","metadata":{"truncated":false},"title":"","time":{"start":1785000000000,"end":1785000000001}},"id":"prt_00000000000000000000","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"tool_use","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_write_file","callID":"call_0000000000000000","state":{"status":"completed","input":{"path":"/tmp/normalized/todo-list.js","content":"function TodoList() { this.items = []; }\\nTodoList.prototype.add = function (item) { this.items.push(item); };\\nmodule.exports = TodoList;\\n"},"output":"wrote 3 lines","metadata":{"truncated":false},"title":"","time":{"start":1785000000000,"end":1785000000001}},"id":"prt_00000000000000000000","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"tool_use","timestamp":1785000001000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_edit_file","callID":"call_0000000000000001","state":{"status":"completed","input":{"path":"/tmp/normalized/todo-list.js","oldText":"function TodoList() { this.items = []; }\\n","newText":"function TodoList() { this.items = []; this.nextId = 0; }\\n"},"output":"edited 1 location","metadata":{"truncated":false},"title":"","time":{"start":1785000001000,"end":1785000001001}},"id":"prt_00000000000000000001","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"tool_use","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_read_file","callID":"call_0000000000000002","state":{"status":"completed","input":{"path":"/tmp/normalized/todo-list.js"},"output":"function TodoList() { this.items = []; this.nextId = 0; }\\nTodoList.prototype.add = function (item) { this.items.push(item); };\\nmodule.exports = TodoList;\\n","metadata":{"truncated":false},"title":"","time":{"start":1785000000000,"end":1785000000001}},"id":"prt_00000000000000000000","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"step_finish","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"id":"prt_00000000000000000000","reason":"tool-calls","messageID":"msg_00000000000000000000","sessionID":"ses_00000000000000000000","type":"step-finish"}}',
)
