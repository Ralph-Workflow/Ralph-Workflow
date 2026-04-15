from __future__ import annotations

from ralph.mcp.tool_coordination import (
    handle_coordinate,
    handle_declare_complete,
    handle_report_progress,
)


class MockSession:
    session_id = "session-1"

    def check_capability(self, _capability: str) -> object:
        return True


class MockWorkspace:
    def absolute_path(self, path: str) -> str:
        return path


def test_report_progress_accepts_injected_timestamp() -> None:
    result = handle_report_progress(
        MockSession(),
        MockWorkspace(),
        {"status": "running", "note": "halfway"},
        now_fn=lambda: 123,
    )

    assert "timestamp=123" in result.content[0].text


def test_declare_complete_accepts_injected_timestamp() -> None:
    result = handle_declare_complete(
        MockSession(),
        MockWorkspace(),
        {"summary": "done"},
        now_fn=lambda: 456,
    )

    assert "timestamp=456" in result.content[0].text


def test_coordinate_accepts_injected_timestamp() -> None:
    result = handle_coordinate(
        MockSession(),
        MockWorkspace(),
        {"action": "sync", "work_unit_id": "u-1", "payload": {"ok": True}},
        now_fn=lambda: 789,
    )

    assert "timestamp=789" in result.content[0].text
