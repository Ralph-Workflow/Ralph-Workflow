from __future__ import annotations

import contextlib
import json
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import AgentRunCtx, PtyExtras, PtyLineReader
from ralph.agents.invoke.attempt_supervision import AttemptSupervisor
from ralph.agents.timeout_clock import SystemClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.recovery.attempt_facts import TerminationIssuer
from tests.agents.invoke.test_pending_subagents_turn_end import (
    _StubCliHandle,
    _spawn_stub,
)

pytestmark = pytest.mark.subprocess_e2e

_SESSION_ID = "sess-pending-completion"
_DISPATCH_ID = "toolu_pending"


def _parent_record(*, content: list[dict[str, object]], role: str, record_type: str) -> str:
    return json.dumps(
        {
            "type": record_type,
            "sessionId": _SESSION_ID,
            "message": {"role": role, "content": content},
        }
    )


def _prepare_transcript(monkeypatch: pytest.MonkeyPatch, workspace: Path) -> Path:
    shadow_home = workspace / "shadow-home"
    project_key = str(workspace.resolve()).replace("/", "-").replace(" ", "-")
    project_dir = shadow_home / ".claude" / "projects" / project_key
    (project_dir / _SESSION_ID / "subagents").mkdir(parents=True)
    transcript_path = project_dir / f"{_SESSION_ID}.jsonl"
    transcript_path.write_text(
        _parent_record(content=[{"type": "text", "text": "start"}], role="user", record_type="user")
        + "\n"
        + _parent_record(
            content=[{"type": "tool_use", "id": _DISPATCH_ID, "name": "Agent", "input": {}}],
            role="assistant",
            record_type="assistant",
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(Path, "home", lambda: shadow_home)
    return transcript_path


def _reader(
    workspace: Path,
    handle: _StubCliHandle,
    supervisor: AttemptSupervisor,
    sentinel_path: Path,
) -> PtyLineReader:
    return PtyLineReader(
        handle,
        "claude",
        AgentRunCtx(
            config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
            show_progress=False,
            extra_env=None,
            workspace_path=workspace,
            policy=TimeoutPolicy(
                idle_timeout_seconds=300.0,
                process_monitor_enabled=False,
                parent_exit_grace_seconds=1.0,
            ),
        ),
        SystemClock(),
        extras=PtyExtras(
            expected_session_id=_SESSION_ID,
            stop_sentinel_path=sentinel_path,
            input_prompt="delegate discovery",
        ),
        supervisor=supervisor,
    )


def _start_reader(reader: PtyLineReader) -> tuple[list[str], threading.Thread]:
    lines: list[str] = []

    def consume() -> None:
        lines.extend(reader.read_lines())

    thread = threading.Thread(target=consume, daemon=True)
    thread.start()
    return lines, thread


def _wait_until(predicate: object, *, detail: str, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if callable(predicate) and predicate():
            return
        threading.Event().wait(0.01)
    pytest.fail(detail)


def _append_result(transcript_path: Path, tool_use_id: str) -> str:
    record = _parent_record(
        content=[{"type": "tool_result", "tool_use_id": tool_use_id, "content": "done"}],
        role="user",
        record_type="user",
    )
    with transcript_path.open("a", encoding="utf-8") as handle:
        handle.write(record + "\n")
    return record


def _cleanup(child: object, master_fd: int, reader_thread: threading.Thread) -> None:
    pid = getattr(child, "pid")
    with contextlib.suppress(Exception):
        os.killpg(pid, signal.SIGKILL)
    getattr(child, "wait")(timeout=5)
    reader_thread.join(timeout=2)
    os.close(master_fd)


def test_stop_hook_does_not_exit_while_subagent_dispatch_is_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript_path = _prepare_transcript(monkeypatch, tmp_path)
    del transcript_path
    master_fd, child, sentinel_path = _spawn_stub(tmp_path)
    handle = _StubCliHandle(master_fd, child)
    reader = _reader(tmp_path, handle, AttemptSupervisor(handle), sentinel_path)
    lines, reader_thread = _start_reader(reader)
    try:
        _wait_until(lambda: _DISPATCH_ID in "".join(lines), detail="dispatch was not observed")
        _wait_until(
            lambda: time.monotonic() > 0 and not sentinel_path.exists(),
            detail="interim Stop sentinel was not consumed",
            timeout=1.2,
        )
        threading.Event().wait(0.7)
        assert child.poll() is None
        assert handle.terminal_reasons == []
        assert reader_thread.is_alive()
    finally:
        _cleanup(child, master_fd, reader_thread)


def test_session_exits_exactly_once_after_pending_dispatch_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript_path = _prepare_transcript(monkeypatch, tmp_path)
    master_fd, child, sentinel_path = _spawn_stub(tmp_path)
    handle = _StubCliHandle(master_fd, child)
    supervisor = AttemptSupervisor(handle)
    reader = _reader(tmp_path, handle, supervisor, sentinel_path)
    lines, reader_thread = _start_reader(reader)
    try:
        _wait_until(lambda: _DISPATCH_ID in "".join(lines), detail="dispatch was not observed")
        threading.Event().wait(0.7)
        assert child.poll() is None
        result_record = _append_result(transcript_path, _DISPATCH_ID)
        _wait_until(lambda: result_record in "".join(lines), detail="matching result was not observed")
        sentinel_path.touch()
        reader_thread.join(timeout=2)

        assert not reader_thread.is_alive()
        assert child.wait(timeout=2) == 0
        assert handle.terminal_reasons == ["interactive_completion", "interactive_completion"]
        assert handle.terminate_issuers == []
        assert supervisor.recorder.termination().issuer is TerminationIssuer.UNKNOWN
    finally:
        if reader_thread.is_alive():
            _cleanup(child, master_fd, reader_thread)
        else:
            os.close(master_fd)


def test_unrelated_tool_result_does_not_clear_pending_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transcript_path = _prepare_transcript(monkeypatch, tmp_path)
    master_fd, child, sentinel_path = _spawn_stub(tmp_path)
    handle = _StubCliHandle(master_fd, child)
    reader = _reader(tmp_path, handle, AttemptSupervisor(handle), sentinel_path)
    lines, reader_thread = _start_reader(reader)
    try:
        _wait_until(lambda: _DISPATCH_ID in "".join(lines), detail="dispatch was not observed")
        threading.Event().wait(0.7)
        unrelated_record = _append_result(transcript_path, "toolu_unrelated")
        _wait_until(
            lambda: unrelated_record in "".join(lines), detail="unrelated result was not observed"
        )
        sentinel_path.touch()
        threading.Event().wait(0.15)

        assert child.poll() is None
        assert handle.terminal_reasons == []
        assert reader_thread.is_alive()
    finally:
        _cleanup(child, master_fd, reader_thread)
