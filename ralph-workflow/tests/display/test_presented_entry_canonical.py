"""P1 (wt-028-display S-10 / AC-11) canonical PresentedEntry tests.

The shared registry's :class:`PresentedEntry` is the single
structured intermediate the live display and the text-first record
writer both consume. Every event produces one entry; identity,
timestamp, severity, phase, cycle, iter, body, and metadata each
appear at most once per entry.

These tests pin the contract for the canonical entry so the live
log and the rendered record stay consistent.
"""

from __future__ import annotations

import re

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_provider import ActivityProvider
from ralph.display.agent_activity_event import AgentActivityEvent
from ralph.display.agent_event_renderer import (
    PresentedEntry,
    build_presented_entry,
)
from ralph.display.record_writer import (
    RenderedRecordWriter,
    safe_id_for,
)


def _event(
    kind: ActivityEventKind,
    content: str | None = None,
    metadata: dict[str, object] | None = None,
) -> AgentActivityEvent:
    return AgentActivityEvent(
        provider=ActivityProvider.CLAUDE,
        kind=kind,
        content=content,
        metadata=metadata or {},
    )


def test_presented_entry_is_a_dataclass_with_required_fields() -> None:
    """The canonical entry carries kind / severity / identity / body."""
    entry = PresentedEntry(
        kind="text",
        severity="info",
        identity="claude",
        body="hello world",
    )
    assert entry.kind == "text"
    assert entry.severity == "info"
    assert entry.identity == "claude"
    assert entry.body == "hello world"


def test_presented_entry_optional_fields_default_to_none() -> None:
    """timestamp / phase / cycle / iter are optional, metadata defaults to empty."""
    entry = PresentedEntry(
        kind="text",
        severity="info",
        identity="claude",
        body="x",
    )
    assert entry.timestamp is None
    assert entry.phase is None
    assert entry.cycle is None
    assert entry.iter is None
    assert entry.metadata == {}


def test_build_presented_entry_pulls_identity_from_unit_id() -> None:
    """``unit_id`` flows into the entry's identity field."""
    event = _event(ActivityEventKind.TEXT, "hi")
    entry = build_presented_entry(event, unit_id="claude")
    assert entry.identity == "claude"
    assert entry.body == "hi"


def test_build_presented_entry_picks_severity_for_errors() -> None:
    """ERROR kind maps to ``error`` severity (consumer-friendly contract)."""
    event = _event(ActivityEventKind.ERROR, "boom")
    entry = build_presented_entry(event, unit_id="claude")
    assert entry.severity == "error"
    assert entry.kind == "error"


def test_build_presented_entry_picks_severity_for_warn_kinds() -> None:
    """TOOL_RESULT / UNKNOWN map to ``warn`` severity (the contract)."""
    for kind in (ActivityEventKind.TOOL_RESULT, ActivityEventKind.UNKNOWN):
        event = _event(kind, "x")
        entry = build_presented_entry(event, unit_id="claude")
        assert entry.severity == "warn", f"{kind} should be warn"


def test_build_presented_entry_picks_info_severity_for_text() -> None:
    """TEXT / THINKING / PROGRESS map to ``info`` severity."""
    for kind in (
        ActivityEventKind.TEXT,
        ActivityEventKind.THINKING,
        ActivityEventKind.PROGRESS,
        ActivityEventKind.LIFECYCLE,
        ActivityEventKind.STATUS,
    ):
        event = _event(kind, "x")
        entry = build_presented_entry(event, unit_id="claude")
        assert entry.severity == "info", f"{kind} should be info"


def test_presented_entry_can_be_record_writer_input(tmp_path) -> None:
    """The record writer consumes :class:`PresentedEntry` directly.

    This pins the AC-11 contract: the same structured intermediate
    feeds both consumers (the live display and the rendered record).
    """
    entry = PresentedEntry(
        kind="text",
        severity="info",
        identity="claude",
        body="hello world",
        timestamp="14:29:03",
        phase="development",
        cycle=3,
        iter="2/4",
    )
    safe_id = safe_id_for("claude", "claude-sonnet-4.5")
    writer = RenderedRecordWriter(
        workspace_root=tmp_path,
        agent="claude",
        model="claude-sonnet-4.5",
    )
    writer.append(entry)
    # The writer accepts a PresentedEntry without crashing -- the
    # contract is "same shape works for both consumers".
    assert writer.pending_lines == 1
    assert writer.path.name == f"{safe_id}.rendered.log"


def test_presented_entry_renders_without_internal_channel_vocabulary(tmp_path) -> None:
    """The canonical entry body must not include ``CONT`` / ``META`` / ``fragments``.

    AC-08: internal event and channel names never reach any surface.
    The record writer output is the canonical test surface.
    """
    entry = PresentedEntry(
        kind="text",
        severity="info",
        identity="claude",
        body="a normal thought passage",
        timestamp="14:29:03",
        phase="development",
    )
    safe_id = safe_id_for("claude", "claude-sonnet-4.5")
    writer = RenderedRecordWriter(
        workspace_root=tmp_path,
        agent="claude",
        model="claude-sonnet-4.5",
    )
    writer.append(entry)
    writer.flush()
    assert writer.path.name == f"{safe_id}.rendered.log"

    # Reload the rendered record from the writer's path and assert
    # no machine vocabulary appears in the human-facing line.
    from pathlib import Path

    record_path = writer.path
    assert record_path is not None
    contents = Path(record_path).read_text(encoding="utf-8")
    for forbidden in ("CONT", "META", "fragments, ", "[thinking-start]", "[thinking-end]"):
        assert forbidden not in contents, (
            f"Rendered record must not contain {forbidden!r}; got: {contents!r}"
        )


def test_presented_entry_identity_does_not_appear_twice(tmp_path) -> None:
    """Identity is single-sourced: appears at most once in the rendered line.

    AC-08: within an entry, identity, timestamp, and severity each
    appear at most once. The record writer's plain-text output is
    the contract.
    """
    entry = PresentedEntry(
        kind="text",
        severity="info",
        identity="claude",
        body="hello",
        timestamp="14:29:03",
    )
    safe_id = safe_id_for("claude", "claude-sonnet-4.5")
    writer = RenderedRecordWriter(
        workspace_root=tmp_path,
        agent="claude",
        model="claude-sonnet-4.5",
    )
    writer.append(entry)
    writer.flush()
    assert writer.path.name == f"{safe_id}.rendered.log"
    from pathlib import Path

    contents = Path(writer.path).read_text(encoding="utf-8")
    # The line should mention ``claude`` once. We count the
    # ``agent=claude`` token specifically (not the bare word).
    agent_token_count = len(re.findall(r"agent=claude\b", contents))
    assert agent_token_count == 1, f"agent=claude appears {agent_token_count} times in: {contents!r}"


def test_presented_entry_timestamp_does_not_appear_twice(tmp_path) -> None:
    """Timestamp is single-sourced: appears at most once in the rendered line."""
    entry = PresentedEntry(
        kind="text",
        severity="info",
        identity="claude",
        body="hello",
        timestamp="14:29:03",
    )
    safe_id = safe_id_for("claude", "claude-sonnet-4.5")
    writer = RenderedRecordWriter(
        workspace_root=tmp_path,
        agent="claude",
        model="claude-sonnet-4.5",
    )
    writer.append(entry)
    writer.flush()
    assert writer.path.name == f"{safe_id}.rendered.log"
    from pathlib import Path

    contents = Path(writer.path).read_text(encoding="utf-8")
    # The timestamp token ``14:29:03`` should appear exactly once.
    ts_count = contents.count("14:29:03")
    assert ts_count == 1, f"timestamp 14:29:03 appears {ts_count} times in: {contents!r}"
