from __future__ import annotations

from ralph.mcp.tools.coordination import (
    handle_coordinate,
    handle_declare_complete,
    handle_read_env,
    handle_report_progress,
)


class MockSession:
    session_id = "session-1"

    def check_capability(self, capability: str) -> object:
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


class MockCapableSession:
    session_id = "session-env"

    def check_capability(self, cap):
        return "approved"


class MockDeniedSession:
    session_id = "denied"

    def check_capability(self, cap):
        return "denied"


def test_read_env_returns_variable_value() -> None:
    result = handle_read_env(
        MockCapableSession(), MockWorkspace(), {"name": "MY_VAR"}, env={"MY_VAR": "hello"}
    )
    assert "MY_VAR=hello" in result.content[0].text


def test_read_env_returns_not_found_when_missing() -> None:
    result = handle_read_env(
        MockCapableSession(), MockWorkspace(), {"name": "MISSING"}, env={}
    )
    assert "MISSING=[not found]" in result.content[0].text


def test_read_env_requires_capability() -> None:
    import pytest

    from ralph.mcp.tools.coordination import CapabilityDeniedError

    with pytest.raises(CapabilityDeniedError):
        handle_read_env(MockDeniedSession(), MockWorkspace(), {"name": "X"}, env={})
