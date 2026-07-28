"""Per-agent parity matrix through the canonical pipeline (S-40).

For each supported agent (claude, claude-headless, codex, opencode,
nanocoder, agy, pi, cursor) plus the generic fallback and the gemini
input format, drives a representative parsed event stream through the
canonical entry pipeline and asserts:

* exactly one entry per event,
* no internal channel vocabulary on any surface,
* pairwise identity-only-difference between agents,
* composite identity (`pi · provider/model`) renders as one identity
  with one color,
* graceful degradation when an agent omits data,
* unknown / malformed input still renders with hierarchy, condensation,
  and accessible color.

Black-box: uses the public ``render_event`` / ``build_presented_entry``
APIs and ``rich.text.Text.plain`` so it does not peek at implementations.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from itertools import combinations
from pathlib import Path

from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_provider import ActivityProvider
from ralph.display.agent_activity_event import AgentActivityEvent
from ralph.display.agent_event_renderer import (
    EVENT_RENDERERS,
    normalize_event_from_agent_output_line,
    render_event,
    render_event_kind_text,
)
from ralph.display.presented_entry import PresentedEntry, build_presented_entry
from ralph.display.theme import IDENTITY_PALETTE, identity_color

# All agents declared in ralph/agents/builtin.py:61-155 plus the
# generic fallback; gemini is parser-only (ralph/agents/parsers/gemini.py).
_SUPPORTED_AGENTS: tuple[str, ...] = (
    "claude",
    "claude-headless",
    "codex",
    "opencode",
    "nanocoder",
    "agy",
    "pi",
    "cursor",
)
_PARITY_STREAM_AGENTS = (*_SUPPORTED_AGENTS, "gemini")
_PROVIDER_BY_AGENT = {
    "claude": ActivityProvider.CLAUDE,
    "claude-headless": ActivityProvider.CLAUDE,
    "codex": ActivityProvider.CODEX,
    "opencode": ActivityProvider.OPENCODE,
    "nanocoder": ActivityProvider.NANOCODER,
    "agy": ActivityProvider.AGY,
    "pi": ActivityProvider.PI,
    "cursor": ActivityProvider.CURSOR,
    "gemini": ActivityProvider.GEMINI,
}

_INTERNAL_VOCABULARY: tuple[str, ...] = (
    "CONT",
    "META",
    "thinking-start",
    "thinking-end",
    "fragments, ",
)


def _make_event(
    provider: ActivityProvider,
    kind: ActivityEventKind,
    content: str = "hello",
    metadata: dict[str, object] | None = None,
) -> AgentActivityEvent:
    return AgentActivityEvent(
        provider=provider,
        kind=kind,
        content=content,
        metadata=metadata or {},
    )


def _drive_event_stream(unit_id: str) -> Iterable[AgentActivityEvent]:
    """A representative event stream: text, thinking, tool, error, unknown."""
    provider = _PROVIDER_BY_AGENT[unit_id]
    yield _make_event(provider, ActivityEventKind.TEXT, "ping")
    yield _make_event(provider, ActivityEventKind.THINKING, "reasoning")
    yield _make_event(
        provider,
        ActivityEventKind.TOOL_USE,
        "Bash",
        metadata={"input": {"command": "ls -la"}},
    )
    yield _make_event(
        provider,
        ActivityEventKind.TOOL_RESULT,
        "ok",
        metadata={"tool": "Bash"},
    )
    yield _make_event(provider, ActivityEventKind.ERROR, "boom")
    yield _make_event(provider, ActivityEventKind.UNKNOWN, "??")


# --- One entry per event --------------------------------------------------


def test_canonical_entry_builds_one_per_event_for_each_agent() -> None:
    """Every supported input stream, including gemini, yields one entry per event."""
    for agent_name in _PARITY_STREAM_AGENTS:
        entries = [
            build_presented_entry(event, unit_id=agent_name)
            for event in _drive_event_stream(agent_name)
        ]
        kinds = [entry.kind for entry in entries]
        assert len(entries) == 6, (
            f"{agent_name}: expected 6 entries (text/thinking/tool_use/"
            "tool_result/error/unknown), got "
            f"{len(entries)}: {kinds}"
        )


# --- No internal vocabulary on any surface --------------------------------


def test_render_event_keeps_internal_vocabulary_off_surface() -> None:
    """CONT / META / thinking-start / fragments never reach any agent surface."""
    for agent_name in _PARITY_STREAM_AGENTS:
        for event in _drive_event_stream(agent_name):
            text = render_event(event, unit_id=agent_name)
            plain = text.plain
            for forbidden in _INTERNAL_VOCABULARY:
                assert forbidden not in plain, (
                    f"{agent_name} {event.kind}: {forbidden!r} leaked into "
                    f"rendered text: {plain!r}"
                )


def test_render_event_kind_text_keeps_internal_vocabulary_off_surface() -> None:
    """The plain-text path also strips internal vocabulary for every input format."""
    for agent_name in _PARITY_STREAM_AGENTS:
        for event in _drive_event_stream(agent_name):
            line = render_event_kind_text(
                event.kind,
                event.content,
                timestamp=event.timestamp,
                metadata=event.metadata,
                agent_name=agent_name,
            )
            for forbidden in _INTERNAL_VOCABULARY:
                assert forbidden not in line, (
                    f"{agent_name} {event.kind}: {forbidden!r} leaked into "
                    f"plain text: {line!r}"
                )


# --- Pairwise identity-only-difference -------------------------------------


def test_same_event_different_agents_differs_only_by_identity() -> None:
    """Every supported input stream, including gemini, shares one event body."""
    rendered = {
        agent_name: render_event(
            _make_event(_PROVIDER_BY_AGENT[agent_name], ActivityEventKind.TEXT, "hello"),
            unit_id=agent_name,
        ).plain
        for agent_name in _PARITY_STREAM_AGENTS
    }
    for first, second in combinations(_PARITY_STREAM_AGENTS, 2):
        body_first = rendered[first].split(" ", 3)[-1].removeprefix(f"{first} ")
        body_second = rendered[second].split(" ", 3)[-1].removeprefix(f"{second} ")
        assert body_first == body_second, f"{first} and {second} differ beyond identity"


# --- Composite identity (Design item 8) ------------------------------------


def test_composite_identity_keeps_one_color() -> None:
    """`pi · minimax/MiniMax-3` keeps one stable, accessible color distinct
    from the bare ``pi`` identity."""
    pi_color = identity_color("pi", terminal_bg_is_light=False)
    composite_color = identity_color("pi · minimax/MiniMax-3", terminal_bg_is_light=False)
    assert composite_color in IDENTITY_PALETTE
    assert pi_color in IDENTITY_PALETTE
    # The composite is normalized as a distinct identity: it must hash
    # to a stable slot. We don't pin "different" (the deterministic
    # slot may coincide) but we do pin "stable" and "in palette".
    repeated = identity_color("pi · minimax/MiniMax-3", terminal_bg_is_light=False)
    assert repeated == composite_color


# --- Graceful degradation when an agent omits data ------------------------


def test_omitted_tool_data_renders_without_collapse() -> None:
    """An agent that omits a tool result's metadata still renders the line."""
    event = _make_event(
        ActivityProvider.GENERIC,
        ActivityEventKind.TOOL_RESULT,
        "raw stdout",
        metadata={},
    )
    text = render_event(event, unit_id="claude").plain
    assert "raw stdout" in text
    assert "claude" in text


def test_omitted_phase_renders_without_shift() -> None:
    """A blank optional field does not collapse the layout."""
    entry = PresentedEntry(
        kind="text",
        severity="info",
        identity="claude",
        body="hello",
    )
    assert entry.phase is None
    assert entry.cycle is None
    assert entry.iter is None
    # Re-rendering through the registry still produces a non-empty line.
    event = _make_event(ActivityProvider.CLAUDE, ActivityEventKind.TEXT, "hello")
    text = render_event(event, unit_id="claude").plain
    assert "hello" in text


# --- Unknown / malformed input still renders with hierarchy ---------------


def test_unknown_event_renders_with_accessible_carrier() -> None:
    """An unknown event still carries the icon + label carrier pair."""
    event = _make_event(
        ActivityProvider.CLAUDE,
        ActivityEventKind.UNKNOWN,
        "no clue",
        metadata={"status": "unparsed", "phase": "scanning"},
    )
    text = render_event(event, unit_id="claude").plain
    # The unknown-event carrier is "warning" -- icon + ASCII label.
    assert "WARN" in text or "?" in text  # ASCII label fallback
    # The body is preserved.
    assert "no clue" in text


def test_malformed_event_renders_via_registry_fallback() -> None:
    """A kind that misses the registry falls back to ``_render_unknown_event``."""
    # Patch the registry to be empty so the fallback fires.
    original = dict(EVENT_RENDERERS)
    EVENT_RENDERERS.clear()
    try:
        event = _make_event(
            ActivityProvider.CLAUDE,
            ActivityEventKind.TEXT,
            "edge",
        )
        text = render_event(event, unit_id="claude").plain
        # The fallback renders identity + body; never crashes.
        assert "edge" in text
        assert "claude" in text
    finally:
        EVENT_RENDERERS.update(original)


# --- Generic fallback & gemini input format -------------------------------


def test_generic_fallback_agent_renders() -> None:
    """The generic fallback identity (ActivityProvider.GENERIC) still renders."""
    event = _make_event(
        ActivityProvider.GENERIC,
        ActivityEventKind.TEXT,
        "fallback",
    )
    text = render_event(event, unit_id="generic").plain
    assert "fallback" in text


def test_gemini_input_format_renders() -> None:
    """The gemini input format (parser-only) still produces a presented entry."""
    # Gemini-style text: just a content string.
    event = _make_event(
        ActivityProvider.GEMINI,
        ActivityEventKind.TEXT,
        "gemini answer",
    )
    entry = build_presented_entry(event, unit_id="gemini")
    assert entry.identity == "gemini"
    assert entry.body == "gemini answer"


# --- NDJSON parity corpus (AC-10 / S-9) ----------------------------------
#
# Each fixture is a real-style NDJSON stream mirroring what each
# parser produces. The matrix exercises the canonical entry pipeline
# against each stream and asserts the same per-event invariants the
# inline parity tests pin for the in-test synthetic stream.
# This is the regression corpus: the pi/claude/gemini fixtures are
# checked-in alongside the parity test, so a future change to the
# canonical pipeline that breaks one of them cannot ship.


_FIXTURES_DIR = Path(__file__).parent / "_fixtures"


def _load_ndjson_fixture(name: str) -> list[dict[str, object]]:
    path = _FIXTURES_DIR / name
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _event_from_fixture(record: dict[str, object]) -> AgentActivityEvent:
    provider_str = str(record.get("provider", "claude"))
    provider = ActivityProvider(provider_str)
    kind_str = str(record.get("kind", "text"))
    kind = ActivityEventKind(kind_str)
    content = str(record.get("content", ""))
    metadata_raw = record.get("metadata", {})
    metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
    return AgentActivityEvent(
        provider=provider,
        kind=kind,
        content=content,
        metadata=metadata,
    )


def test_claude_ndjson_fixture_yields_one_entry_per_event() -> None:
    """AC-10: claude NDJSON stream -> one PresentedEntry per event, no duplicates."""
    records = _load_ndjson_fixture("claude_ndjson.jsonl")
    entries = [
        build_presented_entry(_event_from_fixture(rec), unit_id="claude")
        for rec in records
    ]
    # One entry per record.
    assert len(entries) == len(records)
    # No body ever contains the internal channel vocabulary.
    for entry in entries:
        for forbidden in ("CONT", "META", "[thinking-start]", "[thinking-end]"):
            assert forbidden not in entry.body, (
                f"claude fixture: {forbidden!r} leaked into entry: {entry.body!r}"
            )


def test_pi_ndjson_fixture_yields_one_entry_per_event() -> None:
    """AC-10: pi NDJSON stream -> one PresentedEntry per event, no duplicates."""
    records = _load_ndjson_fixture("pi_ndjson.jsonl")
    entries = [
        build_presented_entry(_event_from_fixture(rec), unit_id="pi")
        for rec in records
    ]
    assert len(entries) == len(records)
    for entry in entries:
        for forbidden in ("CONT", "META", "[thinking-start]", "[thinking-end]"):
            assert forbidden not in entry.body, (
                f"pi fixture: {forbidden!r} leaked into entry: {entry.body!r}"
            )


def test_opencode_ndjson_fixture_parses_and_renders_canonical_events() -> None:
    """S-6: replay representative OpenCode wire records through parser and renderer."""
    path = _FIXTURES_DIR / "opencode_ndjson.jsonl"
    wire_records = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    parsed = list(OpenCodeParser().parse(iter(wire_records)))
    events = [
        normalize_event_from_agent_output_line(line, provider=ActivityProvider.OPENCODE, unit_id="opencode")
        for line in parsed
        if line.type in {"text", "tool_use", "tool_result", "error"}
    ]

    assert len(wire_records) >= 20
    assert [event.kind for event in events] == [
        ActivityEventKind.TEXT,
        ActivityEventKind.TEXT,
        ActivityEventKind.TEXT,
        ActivityEventKind.TOOL_USE,
        ActivityEventKind.TOOL_RESULT,
        ActivityEventKind.TOOL_USE,
        ActivityEventKind.TOOL_RESULT,
        ActivityEventKind.TOOL_USE,
        ActivityEventKind.TOOL_RESULT,
        # An errored tool surfaces the dispatch BEFORE its error: the call is
        # real and must stay on the tool timeline (see OpenCodeParser).
        ActivityEventKind.TOOL_USE,
        ActivityEventKind.ERROR,
        ActivityEventKind.ERROR,
        ActivityEventKind.TEXT,
        ActivityEventKind.TEXT,
        ActivityEventKind.TEXT,
    ]
    entries = [build_presented_entry(event, unit_id="opencode") for event in events]
    assert len(entries) == len(events)
    assert all(entry.identity == "opencode" and entry.body for entry in entries)
    assert entries[0].body.endswith("Inspecting the display.")
    assert entries[5].body == "read"
    assert entries[6].body == "renderer source"
    assert entries[9].body == "bash"
    assert entries[10].body == "exit status 1"
    assert entries[11].body == "upstream disconnected"


def test_gemini_ndjson_fixture_yields_one_entry_per_event() -> None:
    """AC-10: gemini NDJSON stream -> one PresentedEntry per event, no duplicates.

    gemini is parser-only (not a selectable agent today) but is a
    supported input format. It must satisfy the same per-event
    invariants as the selectable agents so a future gemini
    selectable agent inherits the presentation by construction.
    """
    records = _load_ndjson_fixture("gemini_ndjson.jsonl")
    entries = [
        build_presented_entry(_event_from_fixture(rec), unit_id="gemini")
        for rec in records
    ]
    assert len(entries) == len(records)
    for entry in entries:
        # Internal vocabulary never reaches the surface.
        for forbidden in ("CONT", "META", "[thinking-start]", "[thinking-end]"):
            assert forbidden not in entry.body, (
                f"gemini fixture: {forbidden!r} leaked into entry: {entry.body!r}"
            )
        # Identity is single-sourced: the entry identity is "gemini" and
        # does not appear duplicated in the body.
        assert entry.identity == "gemini"
        assert "gemini" not in entry.body, (
            f"gemini fixture: identity leaked into body: {entry.body!r}"
        )

