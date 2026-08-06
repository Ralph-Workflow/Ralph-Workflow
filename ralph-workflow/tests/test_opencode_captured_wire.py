"""Display-fidelity regressions against the real 1.18.14 capture.

The captured wire at ``tests/display/_fixtures/opencode_wire.jsonl``
carries the tool names that the live 1.18.14 binary emits --
``ralph_read_file`` / ``ralph_write_file`` / ``ralph_edit_file`` --
which the parser must normalize at the transport boundary so the
canonical preview payload builder recognizes the canonical
``read_file`` / ``write_file`` / ``edit_file`` shape. These tests
are deliberately black-box: they hit only the public parser API
and assert on the resulting ``AgentOutputLine`` content /
metadata, not on private parser state. The corresponding
synthetic-fixture and event-accounting regressions live in
``tests/test_opencode_display_fidelity.py``.

The captured fixture is inlined as a Python literal below so the
test does not touch the filesystem -- the audit_test_policy
check rejects ``read_text`` in unit tests, and the captured
fixture is small enough to inline. The captured ``ralph_*`` tool
names are intentionally preserved here (the parser normalizes them
at the transport boundary) so the inline copy is the same byte
sequence the file at ``tests/display/_fixtures/opencode_wire.jsonl``
contains -- a hand-edited divergence between the two is itself a
regression caught by the ``test_opencode_wire_is_loadable_json``
sanity check.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.display.preview_payload import payload_from_tool_event

if TYPE_CHECKING:
    from collections.abc import Iterator


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


def _parse(parser: OpenCodeParser, lines: Iterator[str]) -> list:
    return list(parser.parse(lines))


class TestOpenCodeCapturedWire:
    """Display-fidelity regressions against the real 1.18.14 capture."""

    def test_captured_ralph_read_file_routes_to_file_preview(self) -> None:
        """A captured ``ralph_read_file`` tool_use must produce a recognized
        ``file_preview`` envelope after the parser normalizes the
        ``ralph_`` prefix off the tool name."""
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
        # The parser normalizes the ``ralph_`` prefix off the tool
        # name so the display layer sees the canonical name.
        assert tool_uses[0].content == "read_file"
        assert tool_uses[0].metadata["tool"] == "read_file"
        # The raw wire name is preserved for diagnostics.
        assert tool_uses[0].metadata["tool_raw"] == "ralph_read_file"
        # The canonical preview payload is non-None -- the smoking
        # gun for the original defect was a None here.
        payload = payload_from_tool_event("read_file", tool_uses[0].metadata)
        assert payload is not None
        assert payload.operation == "read"

    def test_captured_ralph_write_file_routes_to_syntax_preview(self) -> None:
        """A captured ``ralph_write_file`` tool_use must produce a
        recognized ``syntax_preview`` envelope."""
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
        """A captured ``ralph_edit_file`` tool_use must produce a
        recognized ``replace`` (diff-preview) envelope."""
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
        # The diff preview captures the polarity rows.
        assert len(payload.hunks) == 1
        assert payload.hunks[0].old_text == "function TodoList() {}\n"
        assert payload.hunks[0].new_text == (
            "function TodoList() { this.items = []; }\n"
        )

    def test_captured_full_sequence_exercises_all_three_capabilities(self) -> None:
        """A captured read / write / edit / read sequence must produce
        four ``tool_use`` envelopes, each routed through the canonical
        preview payload builder -- the S-5 contract for SUPPORTED
        capability declarations.
        """
        parser = OpenCodeParser()
        parsed = _parse(parser, _OPENCODE_WIRE_FIXTURE_LINES)
        # The captured fixture's six input frames collapse into 8
        # ``AgentOutputLine`` items (a ``step_start`` is suppressed,
        # the four tool frames each produce a ``tool_use`` and a
        # ``tool_result`` pair, and the final ``step_finish`` is
        # suppressed). Every tool_use must route through the
        # canonical preview payload builder.
        tool_uses = [r for r in parsed if r.type == "tool_use"]
        assert len(tool_uses) == 4
        seen_operations: set[str] = set()
        for line in tool_uses:
            meta = line.metadata or {}
            assert "tool" in meta
            payload = payload_from_tool_event(line.content, meta)
            assert payload is not None, (
                f"Captured tool {line.content!r} (raw "
                f"{meta.get('tool_raw')!r}) failed to route through "
                f"the preview payload builder; the display layer "
                f"would silently render nothing"
            )
            seen_operations.add(payload.operation)
        # All three of the required display surfaces materialize.
        assert {"read", "write", "replace"} <= seen_operations


#: Inlined copy of ``tests/display/_fixtures/opencode_wire.jsonl``.
#: Loaded as a Python literal so the test does not touch the
#: filesystem; the audit_test_policy check rejects ``read_text``
#: in unit tests, and the captured fixture is small enough to
#: inline. The captured ``ralph_*`` tool names are intentionally
#: preserved here (the parser normalizes them at the transport
#: boundary) so the inline copy is the same byte sequence the file
#: contains -- a hand-edited divergence between the two is itself a
#: regression.
_OPENCODE_WIRE_FIXTURE_LINES: tuple[str, ...] = (
    '{"type":"step_start","timestamp":1785000000000,"sessionID":"ses_028696ca8ffebRuJyZ6w0z326r","part":{"id":"prt_00000000000000000000","messageID":"msg_00000000000000000000","sessionID":"ses_028696ca8ffebRuJyZ6w0z326r","snapshot":"b23ca142a0b5e1a59f1b25e638067c99644e041b","type":"step-start"}}',
    '{"type":"tool_use","timestamp":1785000000000,"sessionID":"ses_028696ca8ffebRuJyZ6w0z326r","part":{"type":"tool","tool":"ralph_read_file","callID":"call_0000000000000003","state":{"status":"completed","input":{"path":"/workspace/normalized"},"output":"x = 1\\n","metadata":{"truncated":false},"title":"","time":{"start":1785000000000,"end":1785000000001}},"id":"prt_00000000000000000000","sessionID":"ses_028696ca8ffebRuJyZ6w0z326r","messageID":"msg_00000000000000000000"}}',
    '{"type":"tool_use","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_write_file","callID":"call_0000000000000000","state":{"status":"completed","input":{"path":"/tmp/normalized/todo-list.js","content":"function TodoList() { this.items = []; }\\nTodoList.prototype.add = function (item) { this.items.push(item); };\\nmodule.exports = TodoList;\\n"},"output":"wrote 3 lines","metadata":{"truncated":false},"title":"","time":{"start":1785000000000,"end":1785000000001}},"id":"prt_00000000000000000000","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"tool_use","timestamp":1785000001000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_edit_file","callID":"call_0000000000000001","state":{"status":"completed","input":{"path":"/tmp/normalized/todo-list.js","oldText":"function TodoList() { this.items = []; }\\n","newText":"function TodoList() { this.items = []; this.nextId = 0; }\\n"},"output":"edited 1 location","metadata":{"truncated":false},"title":"","time":{"start":1785000001000,"end":1785000001001}},"id":"prt_00000000000000000001","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"tool_use","timestamp":1785000000000,"sessionID":"ses_028696ca8ffebRuJyZ6w0z326r","part":{"type":"tool","tool":"ralph_read_file","callID":"call_0000000000000002","state":{"status":"completed","input":{"path":"/tmp/normalized/todo-list.js"},"output":"function TodoList() { this.items = []; this.nextId = 0; }\\nTodoList.prototype.add = function (item) { this.items.push(item); };\\nmodule.exports = TodoList;\\n","metadata":{"truncated":false},"title":"","time":{"start":1785000000000,"end":1785000000001}},"id":"prt_00000000000000000000","sessionID":"ses_028696ca8ffebRuJyZ6w0z326r","messageID":"msg_00000000000000000000"}}',
    '{"type":"step_finish","timestamp":1785000000000,"sessionID":"ses_028696ca8ffebRuJyZ6w0z326r","part":{"id":"prt_00000000000000000000","reason":"tool-calls","snapshot":"b23ca142a0b5e1a59f1b25e638067c99644e041b","messageID":"msg_00000000000000000000","sessionID":"ses_028696ca8ffebRuJyZ6w0z326r","type":"step-finish","tokens":{"total":39511,"input":3214,"output":73,"reasoning":0,"cache":{"write":0,"read":36224}},"cost":0.00322524}}',
)
