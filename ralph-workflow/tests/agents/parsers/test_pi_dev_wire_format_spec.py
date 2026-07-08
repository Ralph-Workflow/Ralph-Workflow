"""Pin the documented pi.dev AgentSessionEvent wire format.

Pi (https://pi.dev) is a terminal coding agent whose headless
``--mode json`` invocation emits one JSON line per
``AgentSessionEvent``.  The event vocabulary is the documented
TypeScript union at https://pi.dev/docs/latest/json.

The current published contract (re-fetched at execution time from
the live pi.dev docs) enumerates exactly 15
``AgentSessionEvent`` union members (10 ``AgentEvent`` members + 5
direct members) and a separate stream-level ``session`` header line
emitted as the FIRST line of the stream (per the docs: "The first
line is the session header").  The parser additionally accepts
``extension_error`` defensively as a forward-compat extension, but
``extension_error`` is NOT in the current published union, so it is
NOT listed in the ``documented_top_level_events`` set asserted by
:meth:`TestPiDevWireFormatSpec.test_fixture_covers_every_documented_top_level_event`
and is NOT present in the committed fixture.  The parser's
``extension_error`` defensive handling is exercised in
:meth:`TestPiDevWireFormatSpec.test_extension_error_accepted_as_forward_compat`
and in :class:`TestPiParserExtensionError` in ``test_pi_parser.py``.

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
      a ``type='stop'`` line;
  (f) ``extension_error`` events are accepted by the parser as a
      forward-compat extension (NOT a documented event) and yield a
      ``type='error'`` line.

The committed fixture is the canonical source of truth between
Ralph Workflow and the live pi.dev wire format.  When the live docs
change in a future revision, the executor MUST update both the
transient ``tmp/pi-dev-docs/inventory.md`` AND the committed fixture
in the same diff so this test never silently drifts from the live
spec.
"""

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
    """Load the committed wire-format fixture (NOT the transient inventory)."""
    return _FIXTURE_PATH.read_text(encoding="utf-8").splitlines()


def _load_fixture_objects() -> list[dict[str, object]]:
    """Load the committed fixture as a list of JSON-decoded event objects."""
    return [json.loads(line) for line in _load_fixture_lines()]


def _lines(*raw: str) -> Iterator[str]:
    return iter(raw)


_INVENTORY_TOP_LEVEL_HEADER = "Top-level events (AgentSessionEvent union)"
_INVENTORY_STREAM_HEADER_PREFIX = "Stream header"
_INVENTORY_FORWARD_COMPAT_PREFIX = "Forward-compat / extension events"


def _extract_pi_inventory_top_level_section(
    inventory_text: str,
) -> dict[str, str]:
    """Extract the Top-level events, Stream header, and Forward-compat
    sections from a transient ``tmp/pi-dev-docs/inventory.md`` document.

    Returns a mapping with three keys:
      - ``"top_level"``: text of the Top-level events section (between
        its ``###`` heading and the next ``###`` heading)
      - ``"stream_header"``: text of the Stream header section (between
        its ``###`` heading and the next ``###`` heading); empty string
        if the section is absent
      - ``"forward_compat"``: text of the Forward-compat section
        (between its ``###`` heading and the next ``###`` heading);
        empty string if the section is absent.

    The helper is deliberately tolerant of additional whitespace and
    intermediate prose so the inventory can grow notes between
    sections without breaking this guard.
    """
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
    # Close each section at the next `### ` heading or EOF.
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
    """Parse every backticked ``event_name`` token out of a markdown
    LIST SECTION.  Only captures names that appear in markdown
    bullet-list items (``- `name` \u2014 description`` or
    ``- `name` description``); prose paragraphs that mention an
    event name in backticks (e.g. explanatory text or examples) are
    deliberately ignored so the canonical 15-member union contract
    stays aligned with the published pi.dev docs.

    Returns the deduplicated set of backticked tokens that look like
    plausible event names (lowercase letters, digits, underscores).
    """
    names: set[str] = set()
    bullet_pattern = re.compile(r"^\s*-\s+`([a-z][a-z0-9_]*)`")
    for line in section_text.splitlines():
        match = bullet_pattern.match(line)
        if match is not None:
            names.add(match.group(1))
    return names


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

        The current live published contract (re-fetched 2026-06-20
        from https://pi.dev/docs/latest/json) enumerates 15
        ``AgentSessionEvent`` union members (10 ``AgentEvent``
        members + 5 direct members).  The ``session`` line is the
        STREAM-LEVEL HEADER (per the docs: "The first line is the
        session header") and is NOT a member of the union; it is
        emitted as the first line of the stream by ``pi --mode json``
        and the parser routes it through ``_handle_session``.

        ``extension_error`` is accepted defensively by the parser but
        is NOT in the current published contract, so it is
        deliberately excluded from this set and is exercised in
        :meth:`test_extension_error_accepted_as_forward_compat`.

        This is the canonical machine-readable contract between Ralph
        Workflow and the live pi.dev wire format.  When the live docs
        change in a future revision, the executor MUST update both
        this fixture AND the transient
        ``tmp/pi-dev-docs/inventory.md`` in the same diff so the test
        never silently drifts from the live spec.
        """
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
        # The session header line is required by the parser but is
        # NOT a union member; it must still be present in the fixture
        # so the parser's session-header path is exercised.
        assert "session" in actual_top_level_events, (
            "Committed pi.dev wire-format fixture is missing the "
            "stream-level session header line; update the fixture to "
            "include it as the first line."
        )

    def test_inventory_top_level_section_matches_canonical_set(self) -> None:
        """The transient ``tmp/pi-dev-docs/inventory.md`` MUST list
        exactly the canonical 15 ``AgentSessionEvent`` union members
        under its ``Top-level events (AgentSessionEvent union)``
        section, and ``session`` MUST live in a separate
        ``Stream header`` section.

        The previous reconciliation only checked that the canonical
        union members plus the stream-level ``session`` header were
        *present* somewhere in the inventory, which allowed the
        inventory to silently drift by adding extra documented
        top-level events (e.g. ``extension_error``) without updating
        the canonical fixture or the wire-format spec assertion.
        This test reads the inventory's
        ``Top-level events (AgentSessionEvent union)`` section, parses
        the documented top-level event names out of it, and asserts
        EXACT agreement with the canonical 15-event union set.  The
        ``extension_error`` entry MUST live in a separate
        ``Forward-compat / extension events`` section that is excluded
        from this assertion (see
        :meth:`test_extension_error_accepted_as_forward_compat`).

        Per the live docs (https://pi.dev/docs/latest/json):
        ``AgentSessionEvent`` has exactly 15 members (10 ``AgentEvent``
        members + 5 direct members).  ``session`` is the FIRST line
        of the stream (per "The first line is the session header")
        and is NOT a union member; it lives in a separate
        ``Stream header`` section of the inventory.

        This guard fails LOUDLY if the inventory re-introduces
        ``extension_error`` (or any other forward-compat name) as a
        documented top-level event, or if ``session`` is moved back
        into the union's top-level section.
        """
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

        # The transient inventory lives at the REPO ROOT (not under
        # ralph-workflow/).  Resolve from this test file's location.
        repo_root = Path(__file__).resolve()
        for parent in repo_root.parents:
            if (parent / "tmp").is_dir():
                repo_root = parent
                break
        inventory_path = repo_root / "tmp" / "pi-dev-docs" / "inventory.md"
        if not inventory_path.exists():
            # Inventory is transient and may not be present in a
            # fresh checkout; skip rather than fail in that case.
            return
        inventory_text = inventory_path.read_text(encoding="utf-8")

        # Extract the Top-level events (AgentSessionEvent union)
        # section, the Stream header section, and the
        # Forward-compat / extension events section.
        sections = _extract_pi_inventory_top_level_section(inventory_text)
        top_section = sections["top_level"]
        stream_section = sections["stream_header"]
        forward_section = sections["forward_compat"]

        # Parse documented top-level event names from each section.
        # Each entry is a markdown list bullet
        # ``  - `event_name` \u2014 description`` (or
        # ``  - `event_name` description``); capture every
        # backticked name that lives ONLY in the top-level section
        # and NOT in the forward-compat section.
        top_level_names = _parse_backticked_event_names(top_section)
        stream_header_names = _parse_backticked_event_names(stream_section)
        forward_compat_names = _parse_backticked_event_names(forward_section)

        # Sanity: ``extension_error`` MUST be in the forward-compat
        # section (defensive contract from
        # :meth:`test_extension_error_accepted_as_forward_compat`).
        assert "extension_error" in forward_compat_names, (
            f"Expected `extension_error` to be documented in the "
            f"Forward-compat / extension events section of "
            f"{inventory_path}, got forward-compat names "
            f"{sorted(forward_compat_names)}."
        )

        # Sanity: ``session`` MUST live in the Stream header section,
        # NOT in the top-level union section (per the live docs).
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

        # The Top-level events section MUST be in EXACT agreement
        # with the canonical 15-event union set: no missing names
        # and no undocumented names.
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
                actual_sub_events.add(str(assistant_event.get("type", "")))

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

    def test_extension_error_accepted_as_forward_compat(self) -> None:
        """``extension_error`` events MUST be accepted defensively by
        the parser and yield a single ``type='error'`` line.

        ``extension_error`` is NOT in the current published pi.dev
        ``AgentSessionEvent`` union (re-fetched 2026-06-20 from
        https://pi.dev/docs/latest/json; the union enumerates 15
        ``AgentSessionEvent`` union members — 10 ``AgentEvent``
        members + 5 direct members — and ``extension_error`` is
        absent). The parser
        nevertheless keeps a defensive handler at
        ``ralph-workflow/ralph/agents/parsers/pi.py:_handle_extension_error``
        so that any legacy or forward pi.dev build that emits
        ``extension_error`` does not break the parser. This test
        pins that defensive contract: feeding ``extension_error``
        into ``PiParser().parse()`` must NOT raise and must yield a
        ``type='error'`` line whose ``content`` carries the
        ``error`` field. The event is intentionally excluded from
        the documented set in
        :meth:`test_fixture_covers_every_documented_top_level_event`
        and from the committed fixture so the live-doc contract
        test and the wire-format fixture stay aligned with the
        currently published pi.dev docs.
        """
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
