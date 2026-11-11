from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.agents.parsers.pi import (
    _PI_SILENT_SUB_EVENTS,
    _PI_SILENT_TOP_LEVEL_EVENTS,
    PiParser,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "pi_dev_documented_events.json"


def _load_fixture_lines() -> list[str]:
    return _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()


def _load_fixture_objects() -> list[dict[str, object]]:
    return [json.loads(line) for line in _load_fixture_lines()]


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


_INVENTORY_TOP_LEVEL_HEADER = "Top-level events (AgentSessionEvent union)"
_INVENTORY_STREAM_HEADER_PREFIX = "Stream header"
_INVENTORY_FORWARD_COMPAT_PREFIX = "Forward-compat / extension events"


def _extract_pi_inventory_top_level_section(
    inventory_text: str,
) -> dict[str, str]:
    sections: dict[str, str] = {
        "top_level": "",
        "stream_header": "",
        "forward_compat": "",
    }
    lines = inventory_text.splitlines()
    section_ranges: list[tuple[str, int, int]] = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("### "):
            header_text = stripped[4:].strip()
            section_ranges.append((header_text, index, -1))
    for idx, (header, start, _) in enumerate(section_ranges):
        end = len(lines)
        for later_idx in range(idx + 1, len(section_ranges)):
            _, later_start, _ = section_ranges[later_idx]
            if later_start > start:
                end = later_start
                break
        body = "\n".join(lines[start + 1 : end])
        if header == _INVENTORY_TOP_LEVEL_HEADER:
            sections["top_level"] = body
        elif header.startswith(_INVENTORY_STREAM_HEADER_PREFIX):
            sections["stream_header"] = body
        elif header.startswith(_INVENTORY_FORWARD_COMPAT_PREFIX):
            sections["forward_compat"] = body
    return sections


def _parse_backticked_event_names(section_text: str) -> set[str]:
    names: set[str] = set()
    bullet_pattern = re.compile(r"^\s*-\s+`([a-z][a-z0-9_]*)`")
    for line in section_text.splitlines():
        match = bullet_pattern.match(line)
        if match is not None:
            names.add(match.group(1))
    return names


class TestPiDevWireFormatSpec:
    def test_fixture_is_present(self) -> None:
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
        documented_top_level_events = {
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
        }

        objects = _load_fixture_objects()
        actual_top_level_events = {str(obj.get("type", "")) for obj in objects}
        missing = documented_top_level_events - actual_top_level_events
        assert not missing, (
            f"Committed pi.dev wire-format fixture is missing the "
            f"documented AgentSessionEvent union members: "
            f"{sorted(missing)}. Update the fixture in the same diff "
            f"as the live docs (see tmp/pi-dev-docs/inventory.md)."
        )
        assert "session" in actual_top_level_events, (
            "Committed pi.dev wire-format fixture is missing the "
            "stream-level session header line; update the fixture to "
            "include it as the first line."
        )

    def test_inventory_top_level_section_matches_canonical_set(self) -> None:
        canonical_top_level_events = {
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
        }

        repo_root = Path(__file__).resolve()
        for parent in repo_root.parents:
            if (parent / "tmp").is_dir():
                repo_root = parent
                break
        inventory_path = repo_root / "tmp" / "pi-dev-docs" / "inventory.md"
        if not inventory_path.exists():
            return
        inventory_text = inventory_path.read_text(encoding="utf-8")

        sections = _extract_pi_inventory_top_level_section(inventory_text)
        top_section = sections["top_level"]
        stream_section = sections["stream_header"]
        forward_section = sections["forward_compat"]

        top_level_names = _parse_backticked_event_names(top_section)
        stream_header_names = _parse_backticked_event_names(stream_section)
        forward_compat_names = _parse_backticked_event_names(forward_section)

        assert "extension_error" in forward_compat_names, (
            f"Expected `extension_error` to be documented in the "
            f"Forward-compat / extension events section of "
            f"{inventory_path}, got forward-compat names "
            f"{sorted(forward_compat_names)}."
        )

        assert "session" in stream_header_names, (
            f"Expected `session` to be documented in the Stream "
            f"header section of {inventory_path} (per the live docs "
            f"'The first line is the session header'), got "
            f"stream-header names {sorted(stream_header_names)}."
        )
        assert "session" not in top_level_names, (
            f"`session` is the stream-level header line, NOT a member "
            f"of the AgentSessionEvent union (per the live docs); it "
            f"must NOT be listed in the Top-level events (AgentSessionEvent "
            f"union) section of {inventory_path}."
        )

        missing = canonical_top_level_events - top_level_names
        assert not missing, (
            f"tmp/pi-dev-docs/inventory.md Top-level events section is "
            f"missing the canonical documented events: "
            f"{sorted(missing)}.  Update the inventory to include them."
        )
        extras = top_level_names - canonical_top_level_events
        assert not extras, (
            f"tmp/pi-dev-docs/inventory.md Top-level events section "
            f"lists undocumented top-level event names: "
            f"{sorted(extras)}.  The current published "
            f"AgentSessionEvent union enumerates exactly 15 events "
            f"(re-fetched 2026-06-20 from "
            f"https://pi.dev/docs/latest/json); any extra name must "
            f"be moved into the Forward-compat / extension events "
            f"section instead."
        )

    def test_fixture_covers_documented_sub_event_types(self) -> None:
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
                actual_sub_events.add(str(assistant_event.get("type", "")))

        missing = documented_sub_events - actual_sub_events
        assert not missing, (
            f"Committed pi.dev wire-format fixture is missing the "
            f"documented AssistantMessageEvent sub-event types: "
            f"{sorted(missing)}. Update the fixture in the same diff as "
            f"the live docs (see tmp/pi-dev-docs/inventory.md)."
        )

    def test_parser_does_not_raise_on_documented_events(self) -> None:
        lines = _load_fixture_lines()
        parser = PiParser()
        results = list(parser.parse(iter(lines)))
        assert results, (
            "PiParser must yield a non-empty AgentOutputLine stream for "
            "the committed documented event vocabulary."
        )

    def test_parser_yields_typed_output_for_non_silent_events(self) -> None:
        parser = PiParser()
        results = list(parser.parse(iter(_load_fixture_lines())))

        session_lines = [r for r in results if r.type == "session"]
        assert len(session_lines) >= 1, (
            f"PiParser must yield at least one type='session' line for "
            f"the documented session header event, got {len(session_lines)}"
        )

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
        parser = PiParser()
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

    def test_extension_error_accepted_as_forward_compat(self) -> None:
        parser = PiParser()
        line = json.dumps(
            {
                "type": "extension_error",
                "extensionPath": "/path/to/extension.ts",
                "event": "tool_call",
                "error": "boom-extension",
            }
        )
        results = list(parser.parse(_lines(line)))
        assert len(results) == 1, (
            f"PiParser must yield exactly one AgentOutputLine for the "
            f"forward-compat extension_error event, got {results!r}"
        )
        assert results[0].type == "error", (
            f"PiParser must yield type='error' for the forward-compat "
            f"extension_error event, got {results[0]!r}"
        )
        assert results[0].content == "boom-extension", (
            f"PiParser must propagate the extension_error.error field as "
            f"the AgentOutputLine.content, got {results[0].content!r}"
        )

    def test_silent_sub_events_emit_no_output(self) -> None:
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
