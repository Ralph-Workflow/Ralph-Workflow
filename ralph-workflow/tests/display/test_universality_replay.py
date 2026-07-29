"""Parser-native presentation replay for every supported agent (S-5)."""

from __future__ import annotations

import io
from collections.abc import Callable, Iterable
from datetime import datetime
from pathlib import Path

import pytest
from rich.console import Console

from ralph.agents.parsers import (
    AgentOutputLine,
    AgyParser,
    ClaudeInteractiveParser,
    ClaudeParser,
    CodexParser,
    CursorParser,
    GenericParser,
    NanocoderParser,
    OpenCodeParser,
    PiParser,
)
from ralph.agents.parsers.base import AgentParser
from ralph.agents.parsers.gemini import GeminiParser
from ralph.display.activity_provider import ActivityProvider
from ralph.display.agent_event_renderer import normalize_event_from_agent_output_line
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay

_FIXTURES = Path(__file__).parent / "_fixtures"
_ParserFactory = Callable[[], AgentParser]
_CASES: tuple[tuple[str, ActivityProvider, _ParserFactory], ...] = (
    ("claude", ActivityProvider.CLAUDE, ClaudeParser),
    ("claude-headless", ActivityProvider.CLAUDE, ClaudeParser),
    ("claude-interactive", ActivityProvider.CLAUDE_INTERACTIVE, ClaudeInteractiveParser),
    ("codex", ActivityProvider.CODEX, CodexParser),
    ("opencode", ActivityProvider.OPENCODE, OpenCodeParser),
    ("pi", ActivityProvider.PI, PiParser),
    ("cursor", ActivityProvider.CURSOR, CursorParser),
    ("agy", ActivityProvider.AGY, AgyParser),
    ("nanocoder", ActivityProvider.NANOCODER, NanocoderParser),
    ("generic", ActivityProvider.GENERIC, GenericParser),
    ("gemini", ActivityProvider.GEMINI, GeminiParser),
)


def _wire(name: str) -> list[str]:
    return (_FIXTURES / f"{name}_wire.jsonl").read_text(encoding="utf-8").splitlines()


def _replay(
    name: str,
    provider: ActivityProvider,
    parser_factory: _ParserFactory,
    tmp_path: Path,
    *,
    include_condensation: bool = False,
    wire_lines: Iterable[str] | None = None,
) -> tuple[str, str, list[str]]:
    parser = parser_factory()
    parsed = list(parser.parse(iter(_wire(name) if wire_lines is None else wire_lines)))
    visible = [line for line in parsed if line.type not in {"stop", "status", "session"}]
    output = io.StringIO()
    display = ParallelDisplay(
        make_display_context(console=Console(file=output, force_terminal=False, color_system=None, width=100), env={"CI": "1"}),
        workspace_root=tmp_path,
        clock=lambda: datetime(2026, 7, 25, 9, 30, 0),
        monotonic=lambda: 0.0,
    )
    display.start()
    display.emit_phase_start("development", agent_name=name)
    for line in visible:
        event = normalize_event_from_agent_output_line(line, provider=provider, unit_id=name)
        display.emit_parsed_event(unit_id=name, kind=event.kind, content=event.content, metadata=event.metadata, timestamp=line.timestamp)
    if include_condensation:
        condensation_body = f"CONDENSE-{name} " + "x" * 450
        display.emit_parsed_event(
            unit_id=name,
            kind=normalize_event_from_agent_output_line(
                AgentOutputLine(type="text", content=condensation_body, metadata={}),
                provider=provider,
                unit_id=name,
            ).kind,
            content=condensation_body,
            metadata={},
        )
    display.stop()
    return (
        (tmp_path / ".agent" / "raw" / f"{name}.rendered.log").read_text(encoding="utf-8"),
        output.getvalue(),
        [line.content for line in visible if line.content],
    )


@pytest.mark.parametrize(("name", "provider", "parser_factory"), _CASES)
def test_parser_native_replay_keeps_each_agent_on_the_shared_presentation_path(name: str, provider: ActivityProvider, parser_factory: _ParserFactory, tmp_path: Path) -> None:
    """S-5: wire capture -> parser -> normalizer -> display never becomes a raw dump."""
    record, live, expected_bodies = _replay(name, provider, parser_factory, tmp_path)
    lines = [line for line in record.splitlines() if line.strip()]
    assert expected_bodies
    assert len(lines) == len(expected_bodies) + 1
    assert "role=phase_header" in lines[0]
    assert live
    assert all("role=" in line and "[??:??:??]" not in line for line in lines)
    tool_lines = [line for line in lines if "role=tool_" in line]
    assert not tool_lines or any(line.startswith("  ") for line in tool_lines)
    assert all(body.splitlines()[0] in record and body.splitlines()[0] in live for body in expected_bodies)
    assert all(token not in record and token not in live for token in ("CONT", "META", "thinking-start", "thinking-end"))


@pytest.mark.parametrize(("name", "provider", "parser_factory"), _CASES)
def test_parser_native_replay_condenses_every_agent_payload(
    name: str,
    provider: ActivityProvider,
    parser_factory: _ParserFactory,
    tmp_path: Path,
) -> None:
    """DA-005: every provider's shared path accounts for oversized content."""
    record, live, _ = _replay(
        name, provider, parser_factory, tmp_path, include_condensation=True
    )
    for surface in (record, live):
        assert f"CONDENSE-{name}" in surface
        assert "truncated" in surface
        assert " B, see .agent/raw/" in surface


def test_parser_native_replay_uses_supplied_wire_lines(tmp_path: Path) -> None:
    """DA-001: corpus replay parses supplied captures, not the named fixture."""
    record, _live, expected_bodies = _replay(
        "claude",
        ActivityProvider.CLAUDE,
        ClaudeParser,
        tmp_path,
        wire_lines=_wire("claude")[:1],
    )
    assert len(expected_bodies) == 1
    assert expected_bodies[0] in record


def test_parser_native_generic_malformed_payload_still_has_hierarchy(tmp_path: Path) -> None:
    """S-5: malformed generic input is retained as a structured unknown entry."""
    record, live, expected_bodies = _replay("malformed", ActivityProvider.GENERIC, GenericParser, tmp_path)
    assert expected_bodies == ["{not-json"]
    assert "role=unrecognized" in record
    assert "{not-json" in record
    assert live
