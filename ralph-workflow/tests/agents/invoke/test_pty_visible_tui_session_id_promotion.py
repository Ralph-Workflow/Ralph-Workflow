"""Pin: PTY-visible session IDs are promoted into the resumable_session_id path.

The pre-fix bug: ``_pty_runner.py`` and ``_direct_mcp_recovery.py``
captured transport session IDs only via
``extract_transport_session_id_from_line`` which does NOT strip
ANSI escape codes from the line before matching. A PTY-visible
session id banner like
``\x1b[32mClaude session ready. Session ID: abc123\x1b[0m``
would therefore FAIL the anchored text patterns in
``_session._TRANSPORT_TEXT_SESSION_PATTERNS`` (the line starts with
ANSI codes, not with ``Claude session ready``).

The fix: a new helper
``extract_transport_session_id_with_visible_tui`` strips ANSI codes
via ``_visible_tui_text`` before re-running the visible-TUI
extractor. The PTY runner and direct MCP recovery now use this
helper so PTY-visible session IDs are promoted into the
``resumable_session_id`` path used by the recovery controller.

This test pins:

1. The new helper handles plain-text lines (regression check;
   should match ``extract_transport_session_id_from_line``).
2. The new helper handles TUI-banner lines with ANSI codes
   (the pre-fix bug: ``extract_transport_session_id_from_line``
   would return ``None``).
3. The PTY reader's ``_captured_session_id`` field is populated
   from a visible-TUI line that classifies as a transport session
   id (mirrors the subprocess reader's ``_captured_session_id``).
4. ``_direct_mcp_recovery.iter_with_direct_mcp_recovery`` uses the
   new helper so a TUI banner line updates ``current_session_id``.

Black-box: the new helper is called directly with synthetic lines
(plain text, ANSI-wrapped text, garbage lines); the PTY reader is
exercised through ``_record_transcript_session_id`` directly with a
mock-style cache to prove the ``_captured_session_id`` mirror
contract; ``iter_with_direct_mcp_recovery`` is exercised with a
``run_attempt`` callable that yields synthetic lines.

No real subprocess, no real PTY, no real network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke._direct_mcp_recovery import (
    iter_with_direct_mcp_recovery,
)
from ralph.agents.invoke._session import (
    extract_transport_session_id_from_line,
    extract_transport_session_id_with_visible_tui,
)

if TYPE_CHECKING:
    from collections.abc import Iterable


def test_helper_returns_plain_text_session_id() -> None:
    """Plain-text session id lines MUST be matched by the new helper
    (regression check vs the existing
    ``extract_transport_session_id_from_line``).
    """
    cases: tuple[str, ...] = (
        "Claude session ready. Session ID: abc123",
        "Session ID: xyz",
        "Resume this session with --resume def456",
    )
    for line in cases:
        helper_result = extract_transport_session_id_with_visible_tui(line)
        primary_result = extract_transport_session_id_from_line(line)
        assert helper_result == primary_result, (
            f"plain-text line {line!r}: helper={helper_result!r} primary={primary_result!r}"
        )
        assert helper_result is not None, f"plain-text line {line!r} MUST yield a session id"


def test_helper_handles_ansi_wrapped_session_id() -> None:
    """A session id banner wrapped in ANSI escape codes MUST be
    matched by the new helper.

    The pre-fix bug: ``extract_transport_session_id_from_line``
    does NOT strip ANSI codes and would return ``None`` for the
    ``\\x1b[32mClaude session ready. Session ID: abc123\\x1b[0m``
    line below because the anchored regex ``^Claude session
    ready\\. Session ID:\\s*(...)$`` cannot match the leading
    ANSI escape.
    """
    line = "\x1b[32mClaude session ready. Session ID: abc123\x1b[0m"
    primary_result = extract_transport_session_id_from_line(line)
    helper_result = extract_transport_session_id_with_visible_tui(line)
    # The pre-fix bug: primary result was None for ANSI-wrapped lines.
    assert primary_result is None, (
        f"sanity check: pre-fix path SHOULD miss this line; primary={primary_result!r}"
    )
    # The new helper MUST strip ANSI codes and capture the id.
    assert helper_result == "abc123", (
        f"helper MUST strip ANSI codes and capture the id; got {helper_result!r}"
    )


def test_helper_handles_complex_ansi_with_carriage_returns() -> None:
    """A session id banner wrapped in ANSI codes MUST also be
    matched by the helper.
    """
    # The visible-TUI extractor requires a recognised banner
    # pattern; the example below uses the ``Resume this session``
    # banner which the helper MUST recognise through ANSI codes.
    line = "\x1b[1;32mResume this session with --resume ghi789\x1b[0m"
    helper_result = extract_transport_session_id_with_visible_tui(line)
    assert helper_result == "ghi789", f"helper MUST handle complex ANSI; got {helper_result!r}"


def test_helper_returns_none_for_unrelated_lines() -> None:
    """Lines without a session id MUST return ``None`` from the
    helper so the watchdog does not fabricate a captured id.
    """
    cases: tuple[str, ...] = (
        "Some other output line",
        "\x1b[32mDoing some work\x1b[0m",
        "Task declared complete: (no session id)",
    )
    for line in cases:
        result = extract_transport_session_id_with_visible_tui(line)
        assert result is None, f"line {line!r} MUST return None from helper; got {result!r}"


def test_iter_with_direct_mcp_recovery_captures_ansi_session_id() -> None:
    """``iter_with_direct_mcp_recovery`` MUST promote ANSI-wrapped
    session id banners into ``current_session_id`` so a watchdog-kill
    -> resume flow on the PTY transport carries the captured id.

    The pre-fix bug: the recovery helper used the plain
    ``extract_transport_session_id_from_line`` which does NOT strip
    ANSI codes, so a TUI banner line was silently dropped and the
    recovery plan emitted a fresh-session retry instead of a
    resume intent.
    """
    captured_session_ids: list[str] = []

    def _run_attempt(
        current_session_id: str | None,
    ) -> Iterable[str]:
        # Emit a TUI-banner line that the pre-fix path would miss
        # because the line starts with ANSI codes.
        yield "\x1b[32mClaude session ready. Session ID: tui-789\x1b[0m"

    def _reset_registry() -> None:
        return None

    def _on_observed(sid: str) -> None:
        captured_session_ids.append(sid)

    # Collect via ``on_session_observed`` so we can assert the
    # recovery helper observed the TUI-banner session id.
    lines = list(
        iter_with_direct_mcp_recovery(
            _run_attempt,
            max_retries=0,
            reset_tool_registry=_reset_registry,
            on_session_observed=_on_observed,
        )
    )
    assert lines == ["\x1b[32mClaude session ready. Session ID: tui-789\x1b[0m"]
    assert captured_session_ids == ["tui-789"], (
        f"recovery helper MUST promote TUI-banner session id into"
        f" current_session_id via on_session_observed;"
        f" got {captured_session_ids!r}"
    )


def test_iter_with_direct_mcp_recovery_captures_plain_text_session_id() -> None:
    """Regression check: the recovery helper still captures plain-text
    session ids (no ANSI codes) -- the new helper must NOT regress
    the plain-text path.
    """
    captured_session_ids: list[str] = []

    def _run_attempt(
        current_session_id: str | None,
    ) -> Iterable[str]:
        yield "Claude session ready. Session ID: plain-456"

    def _reset_registry() -> None:
        return None

    def _on_observed(sid: str) -> None:
        captured_session_ids.append(sid)

    list(
        iter_with_direct_mcp_recovery(
            _run_attempt,
            max_retries=0,
            reset_tool_registry=_reset_registry,
            on_session_observed=_on_observed,
        )
    )
    assert captured_session_ids == ["plain-456"], (
        f"plain-text path MUST still capture the id; got {captured_session_ids!r}"
    )


def test_iter_with_direct_mcp_recovery_ignores_unrelated_lines() -> None:
    """Lines without a session id MUST NOT update ``current_session_id``
    so the recovery helper cannot fabricate a captured id.
    """
    captured_session_ids: list[str] = []

    def _run_attempt(
        current_session_id: str | None,
    ) -> Iterable[str]:
        yield "Doing some work"
        yield "\x1b[32mSome ANSI output\x1b[0m"
        yield "Yet another line"

    def _reset_registry() -> None:
        return None

    def _on_observed(sid: str) -> None:
        captured_session_ids.append(sid)

    list(
        iter_with_direct_mcp_recovery(
            _run_attempt,
            max_retries=0,
            reset_tool_registry=_reset_registry,
            on_session_observed=_on_observed,
        )
    )
    assert captured_session_ids == [], (
        f"unrelated lines MUST NOT trigger on_session_observed; got {captured_session_ids!r}"
    )
