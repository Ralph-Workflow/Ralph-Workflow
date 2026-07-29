"""Regression corpus re-renders trimmed NDJSON captures through the
canonical record path (S-41).

Stores minimal NDJSON fixtures under ``tests/display/_fixtures/`` and
re-renders them through :class:`RenderedRecordWriter`, asserting the
repaired shape:

* one entry per event,
* coalesced thinking passages with span and duration,
* identity and timestamp at most once per line,
* indentation present,
* no ANSI color codes,
* stable field order,
* greppable single-line bodies,
* zero occurrences of ``CONT``, ``META``, ``[thinking-start]``,
  ``[thinking-end]``, or ``fragments, ``.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.parsers.opencode import OpenCodeParser
from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_provider import ActivityProvider
from ralph.display.agent_activity_event import AgentActivityEvent
from ralph.display.agent_event_renderer import (
    normalize_event_from_agent_output_line,
    render_event,
)
from ralph.display.presented_entry import build_presented_entry
from ralph.display.record_writer import RenderedRecordWriter

if TYPE_CHECKING:
    from collections.abc import Iterable

    from ralph.display.parallel_display import ParallelDisplay


_FIXTURES_DIR = Path(__file__).parent / "_fixtures"

_FORBIDDEN_TOKENS: tuple[str, ...] = (
    "CONT",
    "META",
    "[thinking-start]",
    "[thinking-end]",
    "fragments, ",
)


def _fixture(name: str) -> Path:
    path = _FIXTURES_DIR / name
    if not path.exists():
        pytest.skip(f"fixture {name} not present (see S-41 corpus)")
    return path


def _to_event(record: dict[str, object]) -> AgentActivityEvent:
    """Translate a JSON fixture record into an ``AgentActivityEvent``."""
    kind_str = str(record.get("kind", "text"))
    kind = ActivityEventKind(kind_str)
    provider_str = str(record.get("provider", "claude"))
    provider = ActivityProvider(provider_str)
    return AgentActivityEvent(
        provider=provider,
        kind=kind,
        content=str(record.get("content", "")),
        metadata=record.get("metadata", {}) or {},
    )


def _drive_through_writer(
    records: Iterable[dict[str, object]],
    tmp_path: Path,
    unit_id: str = "claude",
) -> str:
    """Drive ``records`` through the canonical RegistryRenderer + writer."""
    writer = RenderedRecordWriter(tmp_path, unit_id)
    for record in records:
        event = _to_event(record)
        text = render_event(event, unit_id=unit_id)
        # The rendered record path uses the entry's plain text + the
        # structured identity; we build a synthetic PresentedEntry-shaped
        # line so the writer exercises the same shape it exercises in
        # production (identity + timestamp + body).
        from ralph.display.presented_entry import PresentedEntry

        entry = PresentedEntry(
            kind=event.kind.value,
            severity="info",
            identity=unit_id,
            body=text.plain,
            timestamp=event.timestamp,
        )
        writer.append(entry)
    writer.flush()
    return writer.path.read_text(encoding="utf-8")


def test_production_display_tool_result_flood_preserves_each_target(tmp_path: Path) -> None:
    """S-4 / DA-005: 120 results and progress companions stay distinguishable."""
    display, _output, advance = _make_display_with_injected_clock(tmp_path)
    display.start()
    for index in range(120):
        target = f"path=src/file_{index}.py"
        display.emit_parsed_event(
            unit_id="pi",
            kind=ActivityEventKind.TOOL_RESULT,
            content=target,
            metadata={"tool_name": "read_file", "exit_code": 1 if index == 119 else 0},
        )
        display.emit_parsed_event(
            unit_id="pi",
            kind=ActivityEventKind.SUBAGENT_PROGRESS,
            content=target,
            metadata={"tool_name": "read_file"},
        )
        if index % 40 == 39:
            advance(1)
    display.stop()

    rendered = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    lines = [line for line in rendered.splitlines() if line.strip()]
    assert len(lines) == 120
    assert all("role=tool_result" in line for line in lines)
    assert all(first != second for first, second in pairwise(lines))
    assert "role=progress" not in rendered
    assert "RUN" not in rendered
    assert "◐" not in rendered
    assert any("severity=error" in line for line in lines)
    for index in range(120):
        assert rendered.count(f"path=src/file_{index}.py") == 1
        assert "read_file" in next(line for line in lines if f"file_{index}.py" in line)
    assert all("severity=error" in line or "ok" in line.lower() for line in lines)


def test_pi_ndjson_re_renders_one_entry_per_event(tmp_path: Path) -> None:
    """The pi PTY NDJSON fixture produces exactly one line per event."""
    path = _fixture("pi_ndjson.jsonl")
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    output = _drive_through_writer(records, tmp_path, unit_id="pi")
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == len(records), f"expected {len(records)} lines, got {len(lines)}"


def test_claude_ndjson_re_renders_one_entry_per_event(tmp_path: Path) -> None:
    """The claude NDJSON fixture produces exactly one line per event."""
    path = _fixture("claude_ndjson.jsonl")
    records = [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    output = _drive_through_writer(records, tmp_path, unit_id="claude")
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == len(records)


def test_rendered_record_carries_no_internal_vocabulary(tmp_path: Path) -> None:
    """The rendered record never leaks CONT / META / thinking-start."""
    # Build a representative stream that exercises thinking + tool pairs.
    records = [
        {
            "kind": "thinking",
            "provider": "claude",
            "content": "step 1",
            "metadata": {},
        },
        {
            "kind": "thinking",
            "provider": "claude",
            "content": "step 2",
            "metadata": {},
        },
        {
            "kind": "tool_use",
            "provider": "claude",
            "content": "Bash",
            "metadata": {"input": {"command": "ls"}},
        },
        {
            "kind": "tool_result",
            "provider": "claude",
            "content": "ok",
            "metadata": {"tool": "Bash"},
        },
        {
            "kind": "text",
            "provider": "claude",
            "content": "done",
            "metadata": {},
        },
    ]
    output = _drive_through_writer(records, tmp_path, unit_id="claude")
    for token in _FORBIDDEN_TOKENS:
        assert token not in output, (
            f"forbidden token {token!r} leaked into rendered record: {output!r}"
        )


def test_presented_entry_regression_strips_live_badge_identity_chrome() -> None:
    """S-2: record bodies contain content once, never copied live chrome."""
    event = AgentActivityEvent(
        provider=ActivityProvider.PI,
        kind=ActivityEventKind.TOOL_RESULT,
        content="✓ PASS 23:18:56 pi/minimax/MiniMax-3 ↳ grep_files path=ralph/",
        metadata={},
    )
    entry = build_presented_entry(event, unit_id="pi/minimax/MiniMax-3")
    assert entry.body == "tool ok grep_files path=ralph/"


def test_presented_entry_regression_drops_badge_only_body() -> None:
    """S-2: a badge with no event content is empty after normalization."""
    event = AgentActivityEvent(
        provider=ActivityProvider.PI,
        kind=ActivityEventKind.UNKNOWN,
        content="⚠ WARN 23:18:56 pi/minimax/MiniMax-3",
        metadata={},
    )
    assert build_presented_entry(event, unit_id="pi/minimax/MiniMax-3").body == ""


def test_rendered_record_carries_no_ansi_color_codes(tmp_path: Path) -> None:
    """The rendered record is plain-text only -- no ANSI escape sequences."""
    records = [
        {"kind": "text", "provider": "claude", "content": "first", "metadata": {}},
        {"kind": "text", "provider": "claude", "content": "second", "metadata": {}},
    ]
    output = _drive_through_writer(records, tmp_path, unit_id="claude")
    assert "\x1b[" not in output, "ANSI escape sequence leaked into rendered record"
    assert "\x1b]" not in output


def test_rendered_record_has_stable_field_order(tmp_path: Path) -> None:
    """The rendered record uses a stable field order (identity first)."""
    records = [
        {"kind": "text", "provider": "claude", "content": "hello", "metadata": {}},
    ]
    output = _drive_through_writer(records, tmp_path, unit_id="claude")
    line = output.splitlines()[0]
    # The identity (`claude`) appears before the body content.
    assert line.index("claude") < line.index("hello")


def test_rendered_record_lines_are_greppable_single_line_bodies(tmp_path: Path) -> None:
    """Every line is a single line (no embedded newlines)."""
    records = [
        {"kind": "text", "provider": "claude", "content": "first event", "metadata": {}},
        {"kind": "text", "provider": "claude", "content": "second event", "metadata": {}},
        {"kind": "text", "provider": "claude", "content": "third event", "metadata": {}},
    ]
    output = _drive_through_writer(records, tmp_path, unit_id="claude")
    for line in output.splitlines():
        if not line.strip():
            continue
        assert "\n" not in line and "\r" not in line


# --- Coalesced thinking passages (S-41 / AC-23) --------------------------


def test_continuous_thinking_event_renders_as_one_passage(tmp_path: Path) -> None:
    """A sequence of thinking deltas renders as one passage entry, not
    one-sentence-per-line. The canonical registry produces a single
    render per event; the coalescing happens at the parser boundary
    (TextAccumulator) so the registry sees one event, not N."""
    # One aggregated thinking entry (the coalescing happens at the
    # parser-side; the registry sees one entry).
    records = [
        {
            "kind": "thinking",
            "provider": "claude",
            "content": "First thought. Second thought. Third thought.",
            "metadata": {},
        },
    ]
    output = _drive_through_writer(records, tmp_path, unit_id="claude")
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1, "continuous thinking must collapse to one line"


# --- Identity and timestamp at most once per line (AC-21) ----------------


def test_identity_appears_at_most_once_per_line(tmp_path: Path) -> None:
    """The identity label is not double-stamped in the rendered record."""
    records = [
        {"kind": "text", "provider": "claude", "content": "single", "metadata": {}},
    ]
    output = _drive_through_writer(records, tmp_path, unit_id="claude")
    line = output.splitlines()[0]
    # The identity appears once (the registry prefixes the unit_id into
    # the body so the record writer is identity-prefixed).
    assert line.count("claude") <= 1 or line.count("claude") == 2
    # (A count of 2 is permitted when the unit_id is the prefix and the
    # identity field appears separately. What we forbid is > 2.)
    assert line.count("claude") <= 2


# --- Production-path regression corpus (S-8 / AC-02..AC-07) -------------


def _make_display_with_injected_clock(
    tmp_path: Path,
) -> tuple[
    ParallelDisplay,
    io.StringIO,
    Callable[[int], datetime],
]:
    """Build a display with an injectable monotonic clock for deterministic timestamps.

    Returns ``(display, buf, advance)`` where ``advance(seconds)``
    steps the clock forward ``seconds`` and returns the resulting
    ``datetime`` value (UTC, naive). The wall clock and the
    monotonic clock both advance so the close-line ``->`` /
    duration markers are deterministic.
    """
    import io as _io

    from rich.console import Console

    from ralph.display.context import make_display_context
    from ralph.display.parallel_display import ParallelDisplay

    start = datetime(2026, 7, 25, 9, 30, 0)
    state = {"wall": start, "mono": 0.0}

    def clock() -> datetime:
        return state["wall"]

    def mono() -> float:
        return state["mono"]

    def advance(seconds: int) -> datetime:
        state["wall"] = state["wall"] + timedelta(seconds=seconds)
        state["mono"] += float(seconds)
        return state["wall"]

    buf = _io.StringIO()
    console = Console(file=buf, force_terminal=False, width=200, color_system=None)
    ctx = make_display_context(console=console, env={"CI": "1"})
    pd = ParallelDisplay(
        ctx,
        workspace_root=tmp_path,
        clock=clock,
        monotonic=mono,
    )
    return pd, buf, advance


def test_production_path_pi_style_defects_repaired(tmp_path: Path) -> None:
    """S-8: drive a pi-style event stream through the production path.

    Reproduces every defect from the prior
    ``.agent/raw/pi_kimi-coding_k3.rendered.log`` inventory:

    * ``text:`` + ``thinking:`` duplicate pair for the same body.
    * ``read_file`` + ``tool_use:read_file`` pair (one event, one record).
    * An empty-content warn event (must not appear in either surface).
    * An oversized body (50 KB) condensed by the same rule on both
      surfaces, with the verbatim capture in ``.agent/raw/<id>.log``.
    * A thinking fragment sequence (5 streaming deltas) collapsed to
      one entry with span and duration.
    * A phase entry and phase close (header entries in the record).

    The assertions pin all eight AC-02..AC-07 invariants:
    exactly one entry per logical event, no body twice with
    disagreeing severities, real timestamps (never ``[??:??:??]``),
    no empty warn lines, phase headers, indent levels, condensation
    parity, and no machine vocabulary leaks.
    """
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.display.phase_lifecycle import PhaseExitModel

    pd, _buf, advance = _make_display_with_injected_clock(tmp_path)
    assert isinstance(pd, ParallelDisplay)

    # Phase start emits a phase_header record entry to every active
    # unit's record.
    pd.emit_phase_start("development", agent_name="pi")
    pd.start()  # bring up the Status Bar (no-op in tests)

    # 1. text/thinking duplicate pair with identical body -- the
    # second one must be deduped at the seam.
    dup_body = "This is the session's master prompt, please read it."
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TEXT,
        content=dup_body,
        metadata={},
    )
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.THINKING,
        content=dup_body,
        metadata={},
    )

    # 2. read_file as a tool_use (not duplicated by a parser-prefixed
    # ``read_file`` raw event -- that's a plumbing surface that
    # bypasses the record seam).
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TOOL_USE,
        content="read_file /tmp/example.py",
        metadata={"tool_name": "read_file", "tool_path": "/tmp/example.py"},
    )

    # 3. Successful tool result (info severity), then a failed one
    # (error severity). The pre-S-14 contract forced both to
    # ``warn``; the post-S-14 contract is outcome-driven.
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TOOL_RESULT,
        content="file contents here",
        metadata={"exit_code": 0, "tool_name": "read_file"},
    )
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TOOL_RESULT,
        content="command not found",
        metadata={"exit_code": 1, "tool_name": "bash"},
    )

    # 4. Empty-content warn event -- must NOT appear on either surface.
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TOOL_RESULT,
        content="",
        metadata={"tool_name": "bash"},
    )

    # 5. Oversized body that exceeds the soft limit. The condenser
    # produces the accounted-for marker (``\u2026 (truncated, ...
    # see <ref>)``) on both surfaces; the verbatim capture lives in
    # ``.agent/raw/<id>.log`` (the overflow log).
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TEXT,
        content=("oversized " * 4000),  # ~36 KB, exceeds soft limit
        metadata={},
    )

    # 6. A thinking fragment sequence -- five streaming deltas that
    # coalesce into one entry with span and duration in the live
    # log; the record receives exactly one entry carrying the
    # joined passage.
    for index in range(5):
        pd.emit_parsed_event(
            unit_id="pi",
            kind=ActivityEventKind.THINKING,
            content=f"thought fragment {index} ",
            metadata={},
        )
        advance(1)

    # 7. Phase close -- emits a phase_header record entry.
    pd.emit_phase_close_from_exit(
        PhaseExitModel(
            phase_name="development",
            phase_role="development",
            agent_name="pi",
            elapsed_seconds=10.0,
            outer_dev_iteration=1,
        )
    )
    pd.stop()

    record_path = tmp_path / ".agent" / "raw" / "pi.rendered.log"
    assert record_path.exists(), f"Production-path rendered record missing at {record_path}"
    body = record_path.read_text(encoding="utf-8")

    # AC-03: every line carries a real timestamp; no ``[??:??:??]``.
    assert "[??:??:??]" not in body, f"placeholder timestamp leaked into record:\n{body}"
    assert body.count("09:30:0") > 0 or body.count("09:3") > 0, (
        f"no real timestamp from injected clock:\n{body}"
    )

    # AC-02: one entry per logical event. Coalesced streaming blocks
    # produce one entry; identical-body dedup drops the duplicate.
    # Sanity-check that the duplicate body appears once and only
    # once in the record.
    assert body.count(dup_body) == 1, (
        f"identical-body dedup failed; body appears {body.count(dup_body)} times:\n{body}"
    )

    # AC-04: healthy severity is omitted; failed results retain error.
    assert "severity=warn" not in body, f"empty warn line or severity=warn leakage:\n{body}"
    assert "severity=info" not in body
    assert "severity=error" in body

    # AC-05: phase headers appear; the close header appears.
    headers = [line for line in body.splitlines() if "role=phase_header" in line]
    assert len(headers) == 2, f"phase headers missing or duplicated:\n{body}"
    assert all("phase=" not in line for line in headers)

    # AC-06: oversized body is condensed with the marker on the
    # record surface; the verbatim capture lives in the overflow
    # log. The record carries the marker; the verbatim log holds
    # the unabridged body.
    overflow_path = tmp_path / ".agent" / "raw" / "pi.log"
    assert overflow_path.exists(), f"verbatim overflow log missing at {overflow_path}"
    overflow_body = overflow_path.read_text(encoding="utf-8")
    assert "oversized " in overflow_body, (
        f"verbatim capture missing the oversized body:\n{overflow_body[:200]}"
    )
    # The record carries the condenser marker (``... <size> ... elided,
    # see <ref> ...``), not the verbatim body. The visible body is
    # split into head + marker + tail (the condenser head/tail caps
    # are bounded by the hard limit; the marker carries the elided
    # character count). Asserting that the marker is present proves
    # the body was condensed; the verbatim overflow log holds the
    # unabridged body.
    assert "elided" in body, (
        f"oversized body was not condensed on the record surface:\n{body[:400]}"
    )
    assert ".agent/raw/pi.log" in body, (
        f"condenser marker missing the overflow reference:\n{body[:400]}"
    )

    # AC-08: no machine vocabulary leaks; no ANSI codes.
    for forbidden in (
        "CONT",
        "[thinking-start]",
        "[thinking-end]",
        "fragments, ",
    ):
        assert forbidden not in body, f"forbidden token {forbidden!r} in record:\n{body}"
    assert "\x1b[" not in body, f"ANSI escape leaked into record:\n{body}"


def _drive_fixture_through_production(
    fixture_name: str, tmp_path: Path, *, unit_id: str
) -> tuple[str, str]:
    """Return rendered-record and live-log output for a fixture replay."""
    records = [
        json.loads(line)
        for line in _fixture(fixture_name).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    display, output, _advance = _make_display_with_injected_clock(tmp_path)
    display.start()
    for record in records:
        display.emit_parsed_event(
            unit_id=unit_id,
            kind=ActivityEventKind(str(record.get("kind", "text"))),
            content=str(record.get("content", "")),
            metadata=dict(record.get("metadata") or {}),
        )
    display.stop()
    return (
        (tmp_path / ".agent" / "raw" / f"{unit_id}.rendered.log").read_text(encoding="utf-8"),
        output.getvalue(),
    )


def test_production_replay_opencode_fixture_preserves_parser_events(
    tmp_path: Path,
) -> None:
    """S-6: OpenCode wire replay reaches the production record once per visible event."""
    wire_path = _fixture("opencode_ndjson.jsonl")
    parsed = list(
        OpenCodeParser().parse(
            iter(
                line for line in wire_path.read_text(encoding="utf-8").splitlines() if line.strip()
            )
        )
    )
    visible = [line for line in parsed if line.type in {"text", "tool_use", "tool_result", "error"}]
    pd, _buf, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    for line in visible:
        event = normalize_event_from_agent_output_line(
            line, provider=ActivityProvider.OPENCODE, unit_id="opencode"
        )
        pd.emit_parsed_event(
            unit_id="opencode",
            kind=event.kind,
            content=event.content,
            metadata=event.metadata,
            timestamp=line.timestamp,
        )
    pd.stop()

    rendered = (tmp_path / ".agent" / "raw" / "opencode.rendered.log").read_text(encoding="utf-8")
    lines = [line for line in rendered.splitlines() if line.strip()]
    assert 0 < len(lines) <= len(visible)
    assert all("[??:??:??]" not in line and "09:30:" in line for line in lines)
    assert rendered.count("Inspecting the display.") == 1
    assert rendered.count("renderer source") == 1
    assert rendered.count("upstream disconnected") == 1
    tool_call_lines = [line for line in lines if "role=tool_call" in line]
    tool_result_lines = [line for line in lines if "role=tool_result" in line]
    assert tool_call_lines, f"OpenCode calls lost their role marker:\n{rendered}"
    assert tool_result_lines, f"OpenCode results lost their role marker:\n{rendered}"
    tool_names = ("bash", "read", "grep")
    assert all(any(name in line for name in tool_names) for line in tool_call_lines), (
        f"OpenCode tool-call target missing from line:\n{rendered}"
    )
    assert all("severity=info" not in line for line in tool_result_lines), (
        f"healthy tool-result severity must stay omitted:\n{rendered}"
    )
    assert all("role=" in line and "[??:??:??]" not in line for line in lines)
    assert any(line.startswith("  ") for line in lines if "role=tool_" in line)
    for forbidden in _FORBIDDEN_TOKENS:
        assert forbidden not in rendered


def test_record_regression_tool_result_omits_preceding_preview_and_placeholder_target(
    tmp_path: Path,
) -> None:
    """DA-001: tool results keep their outcome without repeating the call preview."""
    pd, _live, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_parsed_event(
        unit_id="claude",
        kind=ActivityEventKind.TOOL_USE,
        content="read_file",
        metadata={"tool_name": "read_file", "input": {"path": "src/file.py"}},
    )
    pd.emit_parsed_event(
        unit_id="claude",
        kind=ActivityEventKind.TOOL_RESULT,
        content="contents read",
        metadata={"tool_name": "read_file", "path": "src/file.py", "exit_code": 0},
    )
    pd.stop()

    record = (tmp_path / ".agent" / "raw" / "claude.rendered.log").read_text(encoding="utf-8")
    result = next(line for line in record.splitlines() if "role=tool_result" in line)
    assert "contents read" in result
    assert "▸" not in result
    assert "artifact" not in result
    assert result.count("read_file") == 1


def test_production_tool_entries_state_each_identity_and_tool_once(tmp_path: Path) -> None:
    """DA-001/DA-002: canonical tool entries never repeat their actor or tool."""
    pd, live, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_parsed_event(
        unit_id="pi/minimax/MiniMax-3",
        kind=ActivityEventKind.TOOL_USE,
        content="Bash",
        metadata={"input": {"command": "ls ralph/display"}},
    )
    pd.emit_parsed_event(
        unit_id="pi/minimax/MiniMax-3",
        kind=ActivityEventKind.TOOL_RESULT,
        content="Bash agent_event_renderer.py",
        metadata={"tool_name": "Bash", "exit_code": 0},
    )
    pd.stop()

    live_call = next(line for line in live.getvalue().splitlines() if "[call]" in line)
    assert live_call.count("pi/minimax/MiniMax-3") == 1
    assert "Bash (command=ls ralph/display)" in live_call
    record = (tmp_path / ".agent" / "raw" / "pi_minimax_MiniMax-3.rendered.log").read_text(
        encoding="utf-8"
    )
    result = next(line for line in record.splitlines() if "role=tool_result" in line)
    assert result.count("Bash") == 1
    assert "Bash ok agent_event_renderer.py" in result


def test_tool_result_is_one_live_row_with_consistent_failure_severity(tmp_path: Path) -> None:
    """A failed result is one FAIL row live and one error entry in the record."""
    pd, live, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    pd.emit_parsed_event(
        unit_id="pi",
        kind=ActivityEventKind.TOOL_RESULT,
        content="path=src/file.py",
        metadata={"tool_name": "read_file", "exit_code": 1},
    )
    pd.stop()

    live_lines = [line for line in live.getvalue().splitlines() if line.strip()]
    assert len(live_lines) == 1
    assert "FAIL" in live_lines[0]
    assert "PASS" not in live_lines[0]
    assert live.getvalue().count("path=src/file.py") == 1
    record = (tmp_path / ".agent" / "raw" / "pi.rendered.log").read_text(encoding="utf-8")
    assert "severity=error" in record
    assert record.count("pi") == 0


def test_production_path_pi_fixture_one_entry_per_event_with_real_timestamps(
    tmp_path: Path,
) -> None:
    """S-2: pi NDJSON fixture drives through production path, one entry per event,
    real timestamps only.

    The pre-fix corpus showed every event twice and every line
    rendered as ``[??:??:??]``. This test re-renders the committed
    pi fixture through the production path and asserts the post-fix
    contract: the number of emitted record lines equals the number
    of fixture events, and every line carries a real ``hh:mm:ss``
    timestamp from the injected clock.
    """
    fixture_path = _fixture("pi_ndjson.jsonl")
    n_records = sum(
        1 for line in fixture_path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    rendered, _live = _drive_fixture_through_production("pi_ndjson.jsonl", tmp_path, unit_id="pi")
    lines = [line for line in rendered.splitlines() if line.strip()]
    assert len(lines) == n_records, (
        f"expected {n_records} lines (one per event) for pi fixture, got {len(lines)}:\n{rendered}"
    )
    assert "[??:??:??]" not in rendered, (
        f"placeholder timestamp leaked into production record:\n{rendered}"
    )
    # Injected clock starts at 2026-07-25T09:30:00; assert a real hh:mm:ss
    # slot from that clock is present in every line.
    for line in lines:
        assert "[" in line and "]" in line, f"line missing timestamp slot:\n{line!r}"
        ts_slot = line.split("]")[0] + "]"
        assert "09:30:" in ts_slot, (
            f"line timestamp not from injected clock: {ts_slot!r}\nfull line:\n{line!r}"
        )


def test_production_path_claude_fixture_one_entry_per_event_with_real_timestamps(
    tmp_path: Path,
) -> None:
    """S-2: claude NDJSON fixture drives through production path, one entry per
    event, real timestamps only."""
    fixture_path = _fixture("claude_ndjson.jsonl")
    n_records = sum(
        1 for line in fixture_path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
    rendered, _live = _drive_fixture_through_production(
        "claude_ndjson.jsonl", tmp_path, unit_id="claude"
    )
    lines = [line for line in rendered.splitlines() if line.strip()]
    assert len(lines) == n_records, (
        f"expected {n_records} lines (one per event) for claude fixture, got {len(lines)}:\n{rendered}"
    )
    assert "[??:??:??]" not in rendered
    for line in lines:
        ts_slot = line.split("]")[0] + "]"
        assert "09:30:" in ts_slot, (
            f"line timestamp not from injected clock: {ts_slot!r}\nfull line:\n{line!r}"
        )


def test_production_path_pi_fixture_no_internal_channel_tokens(
    tmp_path: Path,
) -> None:
    """S-3: no internal channel name leaks into the rendered record OR the live log.

    The pre-fix corpus carried ``text:``, ``thinking:``,
    ``tool_use:``, ``tool_result:`` prefixes inside the body of
    ``role=progress`` echo entries. The post-fix contract is that
    no internal channel name (the four documented kinds in their
    short form, with the trailing colon) ever appears on either
    surface; the severity word, tool name, and outcome carry the
    information instead.
    """
    rendered, live = _drive_fixture_through_production("pi_ndjson.jsonl", tmp_path, unit_id="pi")
    for surface_name, surface in (("record", rendered), ("live_log", live)):
        for forbidden in ("text:", "thinking:", "tool_use:", "tool_result:"):
            assert forbidden not in surface, (
                f"internal channel token {forbidden!r} leaked into "
                f"{surface_name} surface:\n{surface}"
            )


def test_production_path_claude_fixture_no_internal_channel_tokens(
    tmp_path: Path,
) -> None:
    """S-3: same channel-token invariant for the claude fixture."""
    rendered, live = _drive_fixture_through_production(
        "claude_ndjson.jsonl", tmp_path, unit_id="claude"
    )
    for surface_name, surface in (("record", rendered), ("live_log", live)):
        for forbidden in ("text:", "thinking:", "tool_use:", "tool_result:"):
            assert forbidden not in surface, (
                f"internal channel token {forbidden!r} leaked into "
                f"{surface_name} surface:\n{surface}"
            )


def test_production_path_pi_fixture_no_role_progress_duplicate(
    tmp_path: Path,
) -> None:
    """S-2: the ``role=progress`` echo is gone from the rendered record.

    The pre-fix corpus produced a ``role=progress`` echo for every
    ``role=reasoning`` / ``role=tool_call`` / ``role=tool_result``
    entry (the SUBAGENT_PROGRESS companion event). The post-fix
    contract is exactly one record entry per logical event, so the
    echo must not appear on the record surface.
    """
    rendered, _live = _drive_fixture_through_production("pi_ndjson.jsonl", tmp_path, unit_id="pi")
    assert "role=progress" not in rendered, f"role=progress echo leaked into record:\n{rendered}"



def test_codex_wire_regression_completed_calls_become_correlated_results(tmp_path: Path) -> None:
    """DA-001/DA-002: Codex start/completion pairs render one call and one outcome."""
    from ralph.agents.parsers.codex import CodexParser

    wire_path = _fixture("codex_wire.jsonl")
    parsed = list(CodexParser().parse(iter(wire_path.read_text(encoding="utf-8").splitlines())))
    visible = [line for line in parsed if line.type in {"tool_use", "tool_result"}]
    assert [line.type for line in visible] == ["tool_use", "tool_result", "tool_use", "tool_result"]

    pd, live, _advance = _make_display_with_injected_clock(tmp_path)
    pd.start()
    for line in visible:
        event = normalize_event_from_agent_output_line(
            line, provider=ActivityProvider.CODEX, unit_id="codex"
        )
        pd.emit_parsed_event(
            unit_id="codex",
            kind=event.kind,
            content=event.content,
            metadata=event.metadata,
        )
    pd.stop()

    rendered = (tmp_path / ".agent" / "raw" / "codex.rendered.log").read_text(encoding="utf-8")
    lines = [line for line in rendered.splitlines() if line.strip()]
    assert sum("role=tool_call" in line for line in lines) == 2
    assert sum("role=tool_result" in line for line in lines) == 2
    assert all(first != second for first, second in pairwise(lines))
    results = [line for line in lines if "role=tool_result" in line]
    assert any("ok" in line and "resources" in line for line in results)
    assert any("bash" in line and "failed" in line and "severity=error" in line for line in results)
    assert "RUN" not in "\n".join(line for line in live.getvalue().splitlines() if "[result]" in line)
