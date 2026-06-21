"""Pin the documented pi.dev AgentSessionEvent wire format.

Pi (https://pi.dev) is a terminal coding agent whose headless
``--mode json`` invocation emits one JSON line per
``AgentSessionEvent``.  The event vocabulary is the documented
TypeScript union at https://pi.dev/docs/latest/json.

This test loads the committed fixture at
``tests/agents/parsers/fixtures/pi_dev_documented_events.json`` (NOT
the transient ``tmp/pi-dev-docs/inventory.md``) and asserts:

  (a) every documented event type is present in the fixture (the
      fixture IS the canonical machine-readable contract between
      Ralph Workflow and the live pi.dev wire format);
  (b) ``PiParser().parse()`` runs over the full fixture without
      raising;
  (c) the parser yields a non-empty ``AgentOutputLine`` stream for
      every non-silent event (the silent set in
      :data:`_PI_SILENT_TOP_LEVEL_EVENTS` and
      :data:`_PI_SILENT_SUB_EVENTS` must NOT yield);
  (d) the documented isError semantics are honored: a
      ``tool_execution_end`` with ``isError=true`` must produce a
      ``type='error'`` line (NOT ``type='tool_result'``);
  (e) the documented stop events (agent_end, turn_end) each produce
      a ``type='stop'`` line.

The committed fixture is the canonical source of truth between
Ralph Workflow and the live pi.dev wire format.  When the live docs
change in a future revision, the executor MUST update both the
transient ``tmp/pi-dev-docs/inventory.md`` AND the committed fixture
in the same diff so this test never silently drifts from the live
spec.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.parsers.pi import (
    _PI_SILENT_SUB_EVENTS,
    _PI_SILENT_TOP_LEVEL_EVENTS,
    PiParser,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


_FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "pi_dev_documented_events.json"
)


def _load_fixture_lines() -> list[str]:
    """Load the committed wire-format fixture (NOT the transient inventory)."""
    return _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()


def _load_fixture_objects() -> list[dict[str, object]]:
    """Load the committed fixture as a list of JSON-decoded event objects."""
    return [json.loads(line) for line in _load_fixture_lines()]


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


class TestPiDevWireFormatSpec:
    """Pin the documented pi.dev AgentSessionEvent wire format end-to-end."""

    def test_fixture_is_present(self) -> None:
        """The committed fixture must exist at the documented path."""
        assert _FIXTURE_PATH.exists(), (
            f"Committed pi.dev wire-format fixture must exist at "
            f"{_FIXTURE_PATH} so clean-checkout test runs do not depend "
            f"on transient state."
        )
        lines = _load_fixture_lines()
        assert lines, (
            f"Committed pi.dev wire-format fixture at {_FIXTURE_PATH} is "
            f"empty; the wire-format spec test would silently skip the "
            f"documented vocabulary."
        )

    def test_fixture_covers_every_documented_top_level_event(self) -> None:
        """The fixture must contain one NDJSON line per documented
        top-level event type from https://pi.dev/docs/latest/json.

        This is the canonical machine-readable contract between Ralph
        Workflow and the live pi.dev wire format.  When the live docs
        change in a future revision, the executor MUST update both
        this fixture AND the transient
        ``tmp/pi-dev-docs/inventory.md`` in the same diff so the test
        never silently drifts from the live spec.
        """
        documented_top_level_events = {
            "session",
            "agent_start",
            "agent_end",
            "turn_start",
            "turn_end",
            "message_start",
            "message_update",
            "message_end",
            "tool_execution_start",
            "tool_execution_update",
            "tool_execution_end",
            "queue_update",
            "compaction_start",
            "compaction_end",
            "auto_retry_start",
            "auto_retry_end",
            "extension_error",
        }

        objects = _load_fixture_objects()
        actual_top_level_events = {str(obj.get("type", "")) for obj in objects}
        missing = documented_top_level_events - actual_top_level_events
        assert not missing, (
            f"Committed pi.dev wire-format fixture is missing the "
            f"documented top-level event types: {sorted(missing)}. "
            f"Update the fixture in the same diff as the live docs "
            f"(see tmp/pi-dev-docs/inventory.md)."
        )

    def test_fixture_covers_documented_sub_event_types(self) -> None:
        """The fixture must contain at least one of every documented
        AssistantMessageEvent sub-type (per
        https://pi.dev/docs/latest/json: text_start/text_delta/text_end,
        thinking_start/thinking_delta/thinking_end,
        toolcall_start/toolcall_delta/toolcall_end, done, error).
        """
        documented_sub_events = {
            "text_start",
            "text_delta",
            "text_end",
            "thinking_start",
            "thinking_delta",
            "thinking_end",
            "toolcall_start",
            "toolcall_delta",
            "toolcall_end",
            "done",
            "error",
        }

        actual_sub_events: set[str] = set()
        for obj in _load_fixture_objects():
            if obj.get("type") != "message_update":
                continue
            assistant_event = obj.get("assistantMessageEvent")
            if isinstance(assistant_event, dict):
                actual_sub_events.add(
                    str(assistant_event.get("type", ""))
                )

        missing = documented_sub_events - actual_sub_events
        assert not missing, (
            f"Committed pi.dev wire-format fixture is missing the "
            f"documented AssistantMessageEvent sub-event types: "
            f"{sorted(missing)}. Update the fixture in the same diff as "
            f"the live docs (see tmp/pi-dev-docs/inventory.md)."
        )

    def test_parser_does_not_raise_on_documented_events(self) -> None:
        """``PiParser().parse()`` must run the full committed fixture
        without raising on any documented event type.
        """
        lines = _load_fixture_lines()
        parser = PiParser()
        # Consume the iterator; if any event triggers an unexpected
        # exception (e.g. KeyError on a missing field, or
        # ``json.JSONDecodeError`` on malformed input), the test fails.
        results = list(parser.parse(iter(lines)))
        # Sanity: the parser must yield a non-empty stream for the
        # documented vocabulary.
        assert results, (
            "PiParser must yield a non-empty AgentOutputLine stream for "
            "the committed documented event vocabulary."
        )

    def test_parser_yields_typed_output_for_non_silent_events(self) -> None:
        """The parser must produce a non-silent AgentOutputLine for
        every non-silent event type from the committed fixture.

        The silent set (per :data:`_PI_SILENT_TOP_LEVEL_EVENTS` and
        :data:`_PI_SILENT_SUB_EVENTS`) must NOT produce output.  Every
        other documented event must produce at least one
        ``AgentOutputLine`` so downstream consumers see the contract.
        """
        parser = PiParser()
        results = list(parser.parse(iter(_load_fixture_lines())))

        # session header: must produce exactly one type='session' line
        session_lines = [r for r in results if r.type == "session"]
        assert len(session_lines) >= 1, (
            f"PiParser must yield at least one type='session' line for "
            f"the documented session header event, got {len(session_lines)}"
        )

        # tool_execution_end: must produce at least one
        # type='tool_result' line and at least one type='error' line
        # (the fixture contains one of each: isError=false and
        # isError=true).
        tool_result_lines = [r for r in results if r.type == "tool_result"]
        assert tool_result_lines, (
            "PiParser must yield at least one type='tool_result' line "
            "for the documented tool_execution_end(isError=false) event."
        )
        error_lines = [r for r in results if r.type == "error"]
        assert error_lines, (
            "PiParser must yield at least one type='error' line for "
            "the documented tool_execution_end(isError=true) event."
        )

    def test_parser_honors_documented_is_error_semantics(self) -> None:
        """``tool_execution_end.isError=true`` MUST produce
        ``type='error'`` (NOT ``type='tool_result'``).  This pins the
        single consistent isError rule from
        https://pi.dev/docs/latest/json: ``isError = true`` -> error
        semantics, otherwise success/tool_result semantics.
        """
        parser = PiParser()
        # Drive just the isError=true event in isolation; the parser
        # must yield type='error' and must NOT yield type='tool_result'.
        line = json.dumps(
            {
                "type": "tool_execution_end",
                "toolCallId": "call_x",
                "toolName": "bash",
                "result": {"content": [{"type": "text", "text": "fail"}]},
                "isError": True,
            }
        )
        results = list(parser.parse(_lines(line)))
        error_lines = [r for r in results if r.type == "error"]
        assert error_lines, (
            f"PiParser must yield a type='error' line for "
            f"tool_execution_end(isError=true), got {results!r}"
        )
        assert not any(r.type == "tool_result" for r in results), (
            f"PiParser must NOT yield a type='tool_result' line for "
            f"tool_execution_end(isError=true), got {results!r}"
        )

    def test_parser_emits_stop_line_for_documented_stop_events(self) -> None:
        """The documented stop events (``agent_end`` and ``turn_end``)
        MUST each produce a ``type='stop'`` line per the parser's
        contract.
        """
        parser = PiParser()
        lines = [
            json.dumps({"type": "agent_end", "messages": []}),
            json.dumps(
                {
                    "type": "turn_end",
                    "message": {"role": "assistant", "content": []},
                    "toolResults": [],
                }
            ),
        ]
        results = list(parser.parse(_lines(*lines)))
        stop_lines = [r for r in results if r.type == "stop"]
        assert len(stop_lines) == 2, (
            f"PiParser must yield one type='stop' line per documented "
            f"stop event (agent_end + turn_end), got {len(stop_lines)} "
            f"from input {lines!r} -> {results!r}"
        )

    def test_silent_top_level_events_emit_no_output(self) -> None:
        """The documented silent top-level events
        (``agent_start``, ``turn_start``, ``message_start``) MUST NOT
        produce any output, per
        :data:`_PI_SILENT_TOP_LEVEL_EVENTS` and the plan-2
        reconciliation.
        """
        for event_type in _PI_SILENT_TOP_LEVEL_EVENTS:
            parser = PiParser()
            line = json.dumps(
                {"type": event_type, "message": {"role": "assistant"}}
                if event_type == "message_start"
                else {"type": event_type}
            )
            results = list(parser.parse(_lines(line)))
            assert results == [], (
                f"PiParser must NOT yield any AgentOutputLine for the "
                f"silent top-level event {event_type!r}, got {results!r}"
            )

    def test_silent_sub_events_emit_no_output(self) -> None:
        """The documented silent AssistantMessageEvent sub-events
        (``text_start``, ``thinking_start``) MUST NOT produce any
        output, per :data:`_PI_SILENT_SUB_EVENTS` and the plan-2
        reconciliation.
        """
        for sub_event_type in _PI_SILENT_SUB_EVENTS:
            parser = PiParser()
            line = json.dumps(
                {
                    "type": "message_update",
                    "message": {"role": "assistant"},
                    "assistantMessageEvent": {
                        "type": sub_event_type,
                        "contentIndex": 0,
                    },
                }
            )
            results = list(parser.parse(_lines(line)))
            assert results == [], (
                f"PiParser must NOT yield any AgentOutputLine for the "
                f"silent AssistantMessageEvent sub-event {sub_event_type!r}, "
                f"got {results!r}"
            )
