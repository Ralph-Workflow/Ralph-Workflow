from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.tools import coordination as coordination_module
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ToolContent,
    handle_coordinate,
    handle_declare_complete,
    handle_read_env,
    handle_report_progress,
)
from tests.coordination_mock_capable_session import MockCapableSession
from tests.coordination_mock_session import MockSession
from tests.coordination_mock_workspace import MockWorkspace


def test_declare_complete_writes_sentinel_with_correct_run_id() -> None:
    class _SpyWorkspace:
        def __init__(self) -> None:
            self.requested_paths: list[str] = []

        def absolute_path(self, path: str) -> str:
            self.requested_paths.append(path)
            return f"/abs/{path}"

    workspace = _SpyWorkspace()
    seen: list[tuple[str, str]] = []

    coordination_module._write_completion_sentinel(
        workspace,
        "run-sentinel-id",
        _write_fn=lambda path, payload: seen.append((path, payload)),
    )

    assert workspace.requested_paths == [".agent/completion_seen_run-sentinel-id.json"]
    assert seen[0][0].endswith("completion_seen_run-sentinel-id.json")
    assert json.loads(seen[0][1]) == {"run_id": "run-sentinel-id"}


def test_declare_complete_uses_session_run_id_for_sentinel_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str]] = []

    def fake_write_completion_sentinel(
        workspace: object,
        run_id: str,
        *,
        _write_fn: object = None,
        sentinel_hmac: object = None,
    ) -> None:
        assert isinstance(workspace, MockWorkspace)
        seen.append((workspace.absolute_path(f".agent/completion_seen_{run_id}.json"), run_id))

    monkeypatch.setattr(
        coordination_module, "_write_completion_sentinel", fake_write_completion_sentinel
    )

    result = handle_declare_complete(
        MockSession(),
        MockWorkspace(),
        {"summary": "done"},
        now_fn=lambda: 456,
    )

    assert "timestamp=456" in cast("ToolContent", result.content[0]).text
    assert seen == [(".agent/completion_seen_run-1.json", "run-1")]


def test_declare_complete_threads_broker_secret_to_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RFC-013 P3: when the session carries a broker_secret,
    handle_declare_complete threads it through to _write_completion_sentinel
    so the sentinel payload is HMAC-signed against the secret."""
    captured: dict[str, object] = {}

    def capture_write(
        workspace: object,
        run_id: str,
        *,
        sentinel_hmac: object = None,
        _write_fn: object = None,
    ) -> None:
        captured["workspace"] = workspace
        captured["run_id"] = run_id
        captured["sentinel_hmac"] = sentinel_hmac

    monkeypatch.setattr(
        coordination_module, "_write_completion_sentinel", capture_write
    )

    session = MockSession()
    session.broker_secret = "live-broker-secret-12345"
    handle_declare_complete(session, MockWorkspace(), {"summary": "done"})

    assert captured["run_id"] == "run-1"
    assert captured["sentinel_hmac"] == "live-broker-secret-12345"


def test_declare_complete_without_broker_secret_omits_hmac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the session has no broker_secret, sentinel_hmac=None so the
    pre-P3 contract (no HMAC enforcement) applies."""
    captured: dict[str, object] = {}

    def capture_write(
        workspace: object,
        run_id: str,
        *,
        sentinel_hmac: object = None,
        _write_fn: object = None,
    ) -> None:
        captured["sentinel_hmac"] = sentinel_hmac

    monkeypatch.setattr(
        coordination_module, "_write_completion_sentinel", capture_write
    )

    handle_declare_complete(MockSession(), MockWorkspace(), {"summary": "done"})

    assert captured["sentinel_hmac"] is None


def test_declare_complete_best_effort_when_sentinel_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising_write_completion_sentinel(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("disk full")

    monkeypatch.setattr(
        coordination_module, "_write_completion_sentinel", raising_write_completion_sentinel
    )

    result = handle_declare_complete(
        MockSession(),
        MockWorkspace(),
        {"summary": "done"},
        now_fn=lambda: 456,
    )

    assert "timestamp=456" in cast("ToolContent", result.content[0]).text


def test_report_progress_accepts_injected_timestamp() -> None:
    result = handle_report_progress(
        MockSession(),
        MockWorkspace(),
        {"status": "running", "note": "halfway"},
        now_fn=lambda: 123,
    )

    assert "timestamp=123" in cast("ToolContent", result.content[0]).text


def test_write_completion_sentinel_durable_fallback_when_db_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RFC-013 P3 durable-fallback: when ``RunStateDB`` raises
    ``sqlite3.Error`` (locked / corrupt / unsupported WAL), the
    completion sentinel MUST land in the legacy
    ``.agent/completion_seen_<run_id>.json`` file path so the completion
    gate still has durable evidence.
    """

    class _RootWorkspace:
        def __init__(self, root: Path) -> None:
            self.root = root

        def absolute_path(self, path: str) -> str:
            return str(self.root / path)

    workspace = _RootWorkspace(tmp_path)

    def _raise_sqlite(*args: object, **kwargs: object) -> object:
        raise sqlite3.DatabaseError("locked")

    monkeypatch.setattr(coordination_module, "RunStateDB", _raise_sqlite)

    # Must not raise; the durable fallback is the legacy file.
    coordination_module._write_completion_sentinel(workspace, "run-fallback-1")

    legacy_path = (
        tmp_path / ".agent" / "completion_seen_run-fallback-1.json"
    )
    assert legacy_path.exists()
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-fallback-1"


def test_write_completion_sentinel_durable_fallback_with_hmac(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same durable-fallback contract, but threaded with the broker secret
    so the HMAC is bound and a forged secret cannot read it back."""

    class _RootWorkspace:
        def __init__(self, root: Path) -> None:
            self.root = root

        def absolute_path(self, path: str) -> str:
            return str(self.root / path)

    workspace = _RootWorkspace(tmp_path)

    def _raise_sqlite(*args: object, **kwargs: object) -> object:
        raise sqlite3.DatabaseError("locked")

    monkeypatch.setattr(coordination_module, "RunStateDB", _raise_sqlite)

    coordination_module._write_completion_sentinel(
        workspace, "run-fallback-hmac", sentinel_hmac="broker-real"
    )

    legacy_path = (
        tmp_path / ".agent" / "completion_seen_run-fallback-hmac.json"
    )
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-fallback-hmac"
    expected_hmac = __import__("hmac").new(
        b"broker-real", b"run-fallback-hmac", __import__("hashlib").sha256
    ).hexdigest()
    assert payload["hmac"] == expected_hmac


def test_coordinate_accepts_injected_timestamp() -> None:
    result = handle_coordinate(
        MockSession(),
        MockWorkspace(),
        {"action": "sync", "work_unit_id": "u-1", "payload": {"ok": True}},
        now_fn=lambda: 789,
    )

    assert "timestamp=789" in cast("ToolContent", result.content[0]).text


class MockDeniedSession:
    session_id = "denied"
    run_id = "denied-run"

    def check_capability(self, capability: str) -> object:
        return "denied"


def test_read_env_returns_variable_value() -> None:
    result = handle_read_env(
        MockCapableSession(), MockWorkspace(), {"name": "MY_VAR"}, env={"MY_VAR": "hello"}
    )
    assert "MY_VAR=hello" in cast("ToolContent", result.content[0]).text


def test_read_env_returns_not_found_when_missing() -> None:
    result = handle_read_env(MockCapableSession(), MockWorkspace(), {"name": "MISSING"}, env={})
    assert "MISSING=[not found]" in cast("ToolContent", result.content[0]).text


def test_read_env_requires_capability() -> None:
    with pytest.raises(CapabilityDeniedError):
        handle_read_env(MockDeniedSession(), MockWorkspace(), {"name": "X"}, env={})
