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

from ralph.display.activity_event_kind import ActivityEventKind
from ralph.display.activity_provider import ActivityProvider
from ralph.display.agent_activity_event import AgentActivityEvent
from ralph.display.presented_entry import PresentedEntry, build_presented_entry
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
    """S-14 (AC-04): TOOL_RESULT / UNKNOWN default to ``info`` (missing-data graceful).

    The pre-S-14 contract (TOOL_RESULT / UNKNOWN \u2192 ``warn``)
    was wrong: severity must reflect outcome, not kind. A
    successful tool result is ``info``; an absent exit code is
    treated as "data missing, do not invent a failure" and
    stays ``info``. The ``is_error`` flag, a nonzero exit code,
    or a present ``error`` / ``stderr`` payload flips the
    verdict to ``error``.
    """
    for kind in (ActivityEventKind.TOOL_RESULT, ActivityEventKind.UNKNOWN):
        event = _event(kind, "x")
        entry = build_presented_entry(event, unit_id="claude")
        assert entry.severity == "info", f"{kind} without outcome metadata should be info"


def test_build_presented_entry_tool_result_severity_by_outcome() -> None:
    """S-14 (AC-04): TOOL_RESULT severity is driven by outcome metadata.

    A successful tool result (zero exit code, no error flag)
    renders ``info``; a failed one (nonzero exit code, or
    ``is_error=True``, or a present ``error`` / ``stderr``
    payload) renders ``error``. The same content never appears
    twice with disagreeing severities.
    """
    success = _event(
        ActivityEventKind.TOOL_RESULT,
        "ok",
        metadata={"exit_code": 0, "tool_name": "read_file"},
    )
    failure_exit = _event(
        ActivityEventKind.TOOL_RESULT,
        "fail",
        metadata={"exit_code": 1, "tool_name": "bash"},
    )
    failure_flag = _event(
        ActivityEventKind.TOOL_RESULT,
        "fail",
        metadata={"is_error": True, "tool_name": "bash"},
    )
    failure_stderr = _event(
        ActivityEventKind.TOOL_RESULT,
        "fail",
        metadata={"stderr": "command not found", "tool_name": "bash"},
    )
    assert build_presented_entry(success, unit_id="c").severity == "info"
    assert build_presented_entry(failure_exit, unit_id="c").severity == "error"
    assert build_presented_entry(failure_flag, unit_id="c").severity == "error"
    assert build_presented_entry(failure_stderr, unit_id="c").severity == "error"


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


def test_presented_entry_event_row_omits_header_identity(tmp_path) -> None:
    """Identity belongs to the phase header, not an ordinary event row.

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
    assert "agent=claude" not in contents
    assert "role=agent_text" in contents


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


def test_presented_entry_default_indent_level_is_zero() -> None:
    """S-12 (wt-028-display AC-07): the canonical entry carries indent_level.

    Default is 0; the record writer produces an unindented line.
    The structural role defaults to ``agent_text`` so existing
    entries keep their pre-S-12 contract.
    """
    entry = build_presented_entry(_event(ActivityEventKind.TEXT, "hi"), unit_id="claude")
    assert entry.indent_level == 0
    assert entry.grouping_role == "agent_text"


def test_tool_call_vs_tool_result_have_different_indent_levels() -> None:
    """S-12 (wt-028-display AC-07): a tool result hangs under its call.

    The product criteria pin: a tool result line is indented
    deeper than its call. ``_KIND_TO_GROUPING`` is the single
    source of truth.
    """
    call = build_presented_entry(_event(ActivityEventKind.TOOL_USE, "read"), unit_id="c")
    result = build_presented_entry(_event(ActivityEventKind.TOOL_RESULT, "ok"), unit_id="c")
    assert call.indent_level == 0
    assert result.indent_level == 1
    assert result.indent_level > call.indent_level
    assert result.grouping_role == "tool_result"


def test_thinking_entries_are_subordinated() -> None:
    """S-12 (wt-028-display AC-07): reasoning reads as one subordinated passage.

    THINKING gets a level-1 ``reasoning`` role so a single
    coalesced reasoning block still carries its structural
    position when emitted as a presented entry.
    """
    entry = build_presented_entry(
        _event(ActivityEventKind.THINKING, "one thought"), unit_id="c"
    )
    assert entry.indent_level == 1
    assert entry.grouping_role == "reasoning"


def test_record_writer_hangs_tool_result_under_call(tmp_path) -> None:
    """S-12 (wt-028-display AC-07): record carries hierarchy via indentation.

    The record writer outputs a level-0 line for the call and a
    level-1 line for the result. A grep for ``role=tool_result``
    finds the nested entry without depending on column positions.
    """
    from pathlib import Path

    call = PresentedEntry(
        kind="tool_use",
        severity="info",
        identity="claude",
        body="read status_bar.py",
        timestamp="14:29:04",
    )
    result = PresentedEntry(
        kind="tool_result",
        severity="info",
        identity="claude",
        body="214 lines",
        timestamp="14:29:04",
        indent_level=1,
        grouping_role="tool_result",
    )
    writer = RenderedRecordWriter(tmp_path, "claude", model="claude-sonnet-4.5")
    writer.append(call)
    writer.append(result)
    writer.flush()
    contents = Path(writer.path).read_text(encoding="utf-8")
    lines = contents.splitlines()
    # The call line is at column 0; the result line is indented
    # by the indent width.
    assert lines[0].startswith("[14:29:04]")
    assert lines[1].startswith(" " * 2 + "[14:29:04]"), (
        f"Tool result line must be indented under its call; got: {lines[1]!r}"
    )
    assert "role=tool_result" in contents


def test_record_writer_invalid_indent_level_falls_back_to_zero() -> None:
    """S-12 (wt-028-display AC-07): the writer never crashes on bad input.

    A non-int ``indent_level`` clamps to 0 so a future event
    kind with a typo cannot crash the writer.
    """
    from ralph.display.record_writer import _format_entry_line

    # Force a bad value through the dict-like contract.
    bad_entry = {"kind": "text", "severity": "info", "identity": "c", "body": "hi",
                 "timestamp": "14:29:03", "indent_level": "two"}
    line = _format_entry_line(bad_entry)
    assert line.startswith("[14:29:03]"), f"bad indent must clamp to 0; got: {line!r}"


# ---------------------------------------------------------------------------
# AC-07 (wt-028-display S-6): space-less parser channel tokens, sync test
# ---------------------------------------------------------------------------


def test_presented_entry_strips_space_less_text_prefix() -> None:
    """AC-07: ``text:hello`` (no space) is stripped at the PresentedEntry seam.

    The two prefix tables are declared separately to avoid a circular
    import between agent_event_renderer and presented_entry; this
    test pins the behavioral contract that the PresentedEntry seam
    also recognizes the space-less form so neither surface can leak.
    """
    entry = build_presented_entry(
        _event(ActivityEventKind.TEXT, "text:hello world"),
        unit_id="pi",
    )
    assert "text:hello" not in entry.body
    assert "hello world" in entry.body


def test_parser_channel_prefix_tables_stay_in_sync() -> None:
    """AC-07: the live log and rendered record share a single prefix table.

    wt-028-display S-5: the renderer and the presented_entry used
    to declare their prefix tables separately to avoid a circular
    import. After S-5 both modules import the canonical table from
    a single leaf module (:mod:`ralph.display._channel_prefix_stripper`).
    This black-box test pins the convergence through the public
    surface only: the same prefix list (spaced + space-less) must
    be stripped to the same body by both surfaces -- without
    importing the private leaf module directly (the repo-structure
    audit disallows ``from ralph.display._...`` from the test
    tree, so the convergence is verified through observable
    behavior rather than object identity).

    Both renderers delegate to the same stripper function object;
    the helper import address is the convergence surface.
    """
    from ralph.display import agent_event_renderer as renderer
    from ralph.display import presented_entry as presented

    # presented_entry exposes the canonical stripper on its
    # public surface; the renderer re-exports the same callable
    # for downstream code. They must be the SAME object so a
    # future drift cannot surface a divergent stripper in one
    # surface without the other.
    presented_stripper = presented.strip_parser_channel_prefix
    renderer_stripper = renderer.strip_parser_channel_prefix

    # Identity on the function object: the live log and the
    # rendered record delegate to one implementation.
    assert renderer_stripper is presented_stripper, (
        "agent_event_renderer and presented_entry must delegate "
        "to the same stripper function (wt-028-display S-5 single-"
        "channel-prefix-stripper contract)."
    )

    # Behavioural pin: the canonical token set the renderer's
    # documented prefix tables must equal the stripper's observed
    # ``startswith`` truth table. A drift between the renderer
    # table and the stripper's decision would surface here.
    renderer_spaced = renderer._INTERNAL_CHANNEL_PREFIXES
    for token in renderer_spaced:
        assert presented_stripper(f"{token}payload") == "payload", (
            f"Canonical stripper must strip {token!r} prefix from "
            f"a token declared in the renderer prefix table."
        )
    renderer_spaceless = renderer._INTERNAL_CHANNEL_PREFIXES_SPACELESS
    for token in renderer_spaceless:
        # Space-less form requires the remainder to be non-empty
        # AND not begin with whitespace; ``hello`` satisfies both.
        assert presented_stripper(f"{token}hello") == "hello", (
            f"Canonical stripper must strip {token!r} prefix from "
            f"a token declared in the renderer space-less table."
        )

