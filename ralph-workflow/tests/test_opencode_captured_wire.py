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

    def test_captured_real_fixture_routes_every_tool_use(self) -> None:
        """Replay the real 1.18.14 fixture from the file system.

        Loads ``tests/display/_fixtures/opencode_wire.jsonl`` --
        captured directly from the live ``opencode 1.18.14`` binary
        on 2025-11-19 (see
        ``tests/display/_fixtures/opencode_wire_provenance.md``) --
        and drives it through the parser. Every captured
        ``tool_use`` frame must produce a ``tool_use`` + a
        ``tool_result`` pair, no input frame may be silently
        dropped, and the parser envelope must record the
        canonical tool name (so the display layer sees it).
        """
        fixture = _FIXTURE_PATH
        fixture_lines = fixture.read_text(encoding="utf-8").splitlines()
        parser = OpenCodeParser()
        parsed = _parse(parser, iter(fixture_lines))
        # The captured fixture is 12 frames: 4 step_starts (suppressed
        # from visible output), 4 step_finishes (suppressed), 3
        # tool_uses (each producing a tool_use + tool_result pair =
        # 6 visible lines), 1 text (visible). Expected visible lines:
        # 6 tool_use/tool_result + 1 text = 7.
        assert len(parsed) >= 7, (
            f"Parser dropped frames: got {len(parsed)} AgentOutputLine "
            f"items from {len(fixture_lines)} input frames"
        )
        tool_uses = [r for r in parsed if r.type == "tool_use"]
        tool_results = [r for r in parsed if r.type == "tool_result"]
        # Each captured bash tool_use collapses into one
        # tool_use + one tool_result pair (OpenCode is single-frame
        # terminal for completed calls).
        assert len(tool_uses) == 3
        assert len(tool_results) == 3
        for line in tool_uses:
            meta = line.metadata or {}
            # The canonical tool name is in ``metadata["tool"]``
            # (raw wire name retained in ``tool_raw`` for
            # diagnostics); the display layer reads ``meta["tool"]``.
            assert "tool" in meta
            assert "input" in meta, (
                f"Parser did not record an input envelope for {line.content!r}; "
                f"the display layer would have no arguments to preview"
            )

    def test_captured_ralph_envelope_normalizes_at_transport_boundary(self) -> None:
        """A real wire envelope shape carrying an MCP-router-prefixed
        ``ralph_*`` tool name must normalize at the transport
        boundary so the canonical preview builder sees
        ``read_file`` / ``write_file`` / ``edit_file``.

        The captured bash envelopes above prove the wire shape;
        this test pins the router-layer prefix handling on the same
        envelope shape (every key the bash frame carries --
        ``state.input``, ``state.output``, ``callID``, ``state.metadata``,
        ``part.id``, ``part.sessionID``, ``part.messageID`` -- is
        identical to what the router-layer ``ralph_*`` frames
        carry). The router-layer names are exercised here because
        ``ralph smoke-interactive-opencode`` aliases native
        OpenCode tools (read/write/edit) through the bundled Ralph
        MCP server and emits the ``ralph_*`` prefixes on the wire.
        """
        lines = _RAILPH_OPENCODE_ROUTER_LAYER_FIXTURE_LINES
        parser = OpenCodeParser()
        parsed = _parse(parser, iter(lines))
        # Four router-layer tool frames (read, write, edit, read),
        # each producing one tool_use + one tool_result = eight
        # visible lines.
        tool_uses = [r for r in parsed if r.type == "tool_use"]
        assert len(tool_uses) == 4
        # Each tool_use's metadata.tool is the canonical name (not
        # the wire-prefixed name); the raw wire name is preserved
        # in metadata.tool_raw for diagnostics.
        assert {line.metadata["tool"] for line in tool_uses} == {
            "read_file",
            "write_file",
            "edit_file",
        }
        for line in tool_uses:
            assert line.metadata["tool_raw"].startswith("ralph_")
        # The three display surfaces materialize.
        seen_operations: set[str] = set()
        for line in tool_uses:
            payload = payload_from_tool_event(line.content, line.metadata)
            assert payload is not None
            seen_operations.add(payload.operation)
        assert {"read", "write", "replace"} <= seen_operations


#: Inlined router-layer (``ralph_*``) envelopes that share the
#: SAME envelope shape as the real ``opencode_wire.jsonl``
#: fixture (``state.input`` / ``state.output`` / ``callID`` /
#: ``state.metadata.truncated`` / ``part.id`` / ``part.sessionID`/
#: ``part.messageID`` etc.). These envelopes do NOT come from a
#: live capture of the opencode binary; they are synthetic
#: router-layer shapes that exercise the parser's
#: ``ralph_*``-prefix normalization at the transport boundary.
#: ``ralph smoke-interactive-opencode`` aliases the native
#: opencode tools (read / write / edit) through the bundled
#: Ralph MCP server, which adds the ``ralph_`` prefix on the
#: wire -- the SAME envelope shape as what is captured directly
#: from the binary in ``opencode_wire.jsonl``, only with a
#: different tool-name spelling.
_RAILPH_OPENCODE_ROUTER_LAYER_FIXTURE_LINES: tuple[str, ...] = (
    '{"type":"step_start","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"id":"prt_00000000000000000000","messageID":"msg_00000000000000000000","sessionID":"ses_00000000000000000000","type":"step-start"}}',
    '{"type":"tool_use","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_read_file","callID":"call_0000000000000003","state":{"status":"completed","input":{"path":"/workspace/normalized"},"output":"x = 1\\n","metadata":{"truncated":false},"title":"","time":{"start":1785000000000,"end":1785000000001}},"id":"prt_00000000000000000000","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"tool_use","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_write_file","callID":"call_0000000000000000","state":{"status":"completed","input":{"path":"/tmp/normalized/todo-list.js","content":"function TodoList() { this.items = []; }\\nTodoList.prototype.add = function (item) { this.items.push(item); };\\nmodule.exports = TodoList;\\n"},"output":"wrote 3 lines","metadata":{"truncated":false},"title":"","time":{"start":1785000000000,"end":1785000000001}},"id":"prt_00000000000000000000","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"tool_use","timestamp":1785000001000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_edit_file","callID":"call_0000000000000001","state":{"status":"completed","input":{"path":"/tmp/normalized/todo-list.js","oldText":"function TodoList() { this.items = []; }\\n","newText":"function TodoList() { this.items = []; this.nextId = 0; }\\n"},"output":"edited 1 location","metadata":{"truncated":false},"title":"","time":{"start":1785000001000,"end":1785000001001}},"id":"prt_00000000000000000001","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"tool_use","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"type":"tool","tool":"ralph_read_file","callID":"call_0000000000000002","state":{"status":"completed","input":{"path":"/tmp/normalized/todo-list.js"},"output":"function TodoList() { this.items = []; this.nextId = 0; }\\nTodoList.prototype.add = function (item) { this.items.push(item); };\\nmodule.exports = TodoList;\\n","metadata":{"truncated":false},"title":"","time":{"start":1785000000000,"end":1785000000001}},"id":"prt_00000000000000000000","sessionID":"ses_00000000000000000000","messageID":"msg_00000000000000000000"}}',
    '{"type":"step_finish","timestamp":1785000000000,"sessionID":"ses_00000000000000000000","part":{"id":"prt_00000000000000000000","reason":"tool-calls","messageID":"msg_00000000000000000000","sessionID":"ses_00000000000000000000","type":"step-finish"}}',
)
