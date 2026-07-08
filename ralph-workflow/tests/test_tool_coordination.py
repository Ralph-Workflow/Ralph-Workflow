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
    ) -> bool:
        assert isinstance(workspace, MockWorkspace)
        seen.append((workspace.absolute_path(f".agent/completion_seen_{run_id}.json"), run_id))
        return True

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

    monkeypatch.setattr(coordination_module, "_write_completion_sentinel", capture_write)

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

    monkeypatch.setattr(coordination_module, "_write_completion_sentinel", capture_write)

    handle_declare_complete(MockSession(), MockWorkspace(), {"summary": "done"})

    assert captured["sentinel_hmac"] is None


def test_declare_complete_fails_closed_when_sentinel_cannot_be_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed contract: when ``_write_completion_sentinel`` reports
    no durable sentinel was written, ``handle_declare_complete`` MUST
    return ``ToolResult(is_error=True)`` instead of masquerading as a
    success. A successful return without a durable sentinel would
    let the agent falsely claim completion against a sentinel the
    completion gate cannot see."""

    def returning_false_write_completion_sentinel(*args: object, **kwargs: object) -> bool:
        del args, kwargs
        return False

    monkeypatch.setattr(
        coordination_module,
        "_write_completion_sentinel",
        returning_false_write_completion_sentinel,
    )

    result = handle_declare_complete(
        MockSession(),
        MockWorkspace(),
        {"summary": "done"},
        now_fn=lambda: 456,
    )

    assert result.is_error is True
    text = cast("ToolContent", result.content[0]).text
    assert "Task completion rejected" in text
    assert "durable completion sentinel" in text
    assert "timestamp=456" not in text


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

    legacy_path = tmp_path / ".agent" / "completion_seen_run-fallback-1.json"
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

    legacy_path = tmp_path / ".agent" / "completion_seen_run-fallback-hmac.json"
    payload = json.loads(legacy_path.read_text(encoding="utf-8"))
    assert payload["run_id"] == "run-fallback-hmac"
    expected_hmac = (
        __import__("hmac")
        .new(b"broker-real", b"run-fallback-hmac", __import__("hashlib").sha256)
        .hexdigest()
    )
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


def test_read_env_refuses_broker_secret() -> None:
    """A session granted ``env.read`` MUST NOT be able to recover the
    broker-owned HMAC secret even when ``RALPH_BROKER_SECRET`` is in
    the injected environment. Exposing it would let any agent forge
    receipt/sentinel HMACs."""
    result = handle_read_env(
        MockCapableSession(),
        MockWorkspace(),
        {"name": "RALPH_BROKER_SECRET"},
        env={"RALPH_BROKER_SECRET": "topsecret-broker-key"},
    )
    text = cast("ToolContent", result.content[0]).text
    assert "topsecret-broker-key" not in text
    assert "redacted" in text.lower()
    assert "RALPH_BROKER_SECRET=" in text


def test_read_env_value_redacts_broker_secret_when_present() -> None:
    """``read_env_value`` is the seam the handler uses; pin the
    broker-secret redaction there so a future refactor cannot
    accidentally bypass it."""
    assert "topsecret" not in coordination_module.read_env_value(
        {"RALPH_BROKER_SECRET": "topsecret-broker-key"}, "RALPH_BROKER_SECRET"
    )
    assert (
        coordination_module.read_env_value(
            {"RALPH_BROKER_SECRET": "topsecret-broker-key"}, "RALPH_BROKER_SECRET"
        )
        == coordination_module._BROKER_SECRET_DENIED_TEXT
    )


def test_read_env_value_redacts_broker_secret_even_when_absent() -> None:
    """The redacted sentinel is returned even when the variable is
    absent from ``env`` — confirming presence vs absence is preserved
    (the redacted marker is distinct from ``"[not found]"``)."""
    assert (
        coordination_module.read_env_value({}, "RALPH_BROKER_SECRET")
        == coordination_module._BROKER_SECRET_DENIED_TEXT
    )


def test_read_env_value_discloses_non_broker_secrets() -> None:
    """The deny-list in ``_BROKER_SECRET_ENV_NAMES`` is narrowly
    scoped: non-broker env vars are still disclosed normally so the
    agent's diagnostic / orchestrator can keep using ``env.read``
    for unrelated lookups."""
    assert "hello" in coordination_module.read_env_value({"MY_VAR": "hello"}, "MY_VAR")
    assert coordination_module.read_env_value({}, "MY_VAR") == "[not found]"


def test_write_completion_sentinel_returns_true_when_workspace_uses_test_seam() -> None:
    """The ``_write_fn`` test seam persists the payload in memory
    only; the helper must return ``True`` to honor the durable-sentinel
    contract for fail-closed callers like ``handle_declare_complete``."""
    seen: list[tuple[str, str]] = []

    class _Workspace:
        def absolute_path(self, path: str) -> str:
            return f"/abs/{path}"

    result = coordination_module._write_completion_sentinel(
        _Workspace(),
        "run-test-seam",
        _write_fn=lambda path, payload: seen.append((path, payload)),
    )

    assert result is True
    assert len(seen) == 1


def test_write_completion_sentinel_returns_false_when_workspace_is_none() -> None:
    """When the workspace surface is missing (no root to write to),
    the helper returns ``False`` so callers fail closed."""
    result = coordination_module._write_completion_sentinel(None, "run-no-ws")
    assert result is False


def test_write_completion_sentinel_returns_false_when_db_and_legacy_both_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``RunStateDB`` raises AND the legacy-file write also
    fails (the workspace filesystem is unwritable), the helper MUST
    return ``False`` so the handler can refuse to declare the run
    complete. Silent return paths let the agent falsely claim
    completion against a sentinel the completion gate cannot see."""

    class _RootWorkspace:
        def __init__(self, root: Path) -> None:
            self.root = root

        def absolute_path(self, path: str) -> str:
            return str(self.root / path)

    workspace = _RootWorkspace(tmp_path)

    def _raise_sqlite(*_args: object, **_kwargs: object) -> object:
        raise sqlite3.DatabaseError("locked")

    monkeypatch.setattr(coordination_module, "RunStateDB", _raise_sqlite)

    # Force every OS-level mkdir/write to fail too.
    original_mkdir = Path.mkdir
    original_write_text = Path.write_text

    def _raise_oserror_mkdir(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    def _raise_oserror_write(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", _raise_oserror_mkdir)
    monkeypatch.setattr(Path, "write_text", _raise_oserror_write)

    try:
        result = coordination_module._write_completion_sentinel(workspace, "run-fail-closed")
    finally:
        monkeypatch.setattr(Path, "mkdir", original_mkdir)
        monkeypatch.setattr(Path, "write_text", original_write_text)

    assert result is False
    # And neither side wrote anything.
    assert not (tmp_path / ".agent" / "completion_seen_run-fail-closed.json").exists()
