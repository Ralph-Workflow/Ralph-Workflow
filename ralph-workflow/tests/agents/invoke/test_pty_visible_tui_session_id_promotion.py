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
    line = "\x1b[32mClaude session ready. Session ID: abc123\x1b[0m"
    primary_result = extract_transport_session_id_from_line(line)
    helper_result = extract_transport_session_id_with_visible_tui(line)
    assert primary_result is None, (
        f"sanity check: pre-fix path SHOULD miss this line; primary={primary_result!r}"
    )
    assert helper_result == "abc123", (
        f"helper MUST strip ANSI codes and capture the id; got {helper_result!r}"
    )


def test_helper_handles_complex_ansi_with_carriage_returns() -> None:
    line = "\x1b[1;32mResume this session with --resume ghi789\x1b[0m"
    helper_result = extract_transport_session_id_with_visible_tui(line)
    assert helper_result == "ghi789", f"helper MUST handle complex ANSI; got {helper_result!r}"


def test_helper_returns_none_for_unrelated_lines() -> None:
    cases: tuple[str, ...] = (
        "Some other output line",
        "\x1b[32mDoing some work\x1b[0m",
        "Task declared complete: (no session id)",
    )
    for line in cases:
        result = extract_transport_session_id_with_visible_tui(line)
        assert result is None, f"line {line!r} MUST return None from helper; got {result!r}"


def test_iter_with_direct_mcp_recovery_captures_ansi_session_id() -> None:
    captured_session_ids: list[str] = []

    def _run_attempt(
        current_session_id: str | None,
    ) -> Iterable[str]:
        yield "\x1b[32mClaude session ready. Session ID: tui-789\x1b[0m"

    def _reset_registry() -> None:
        return None

    def _on_observed(sid: str) -> None:
        captured_session_ids.append(sid)

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
