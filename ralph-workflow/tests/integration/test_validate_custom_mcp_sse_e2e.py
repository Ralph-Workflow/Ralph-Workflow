"""End-to-end validation of legacy HTTP+SSE upstream MCP support."""

from __future__ import annotations

import contextlib
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from io import StringIO
from textwrap import dedent
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.pipeline import runner as runner_module
from ralph.process.manager import ProcessTerminationError, get_process_manager

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(30)]


@pytest.fixture(autouse=True)
def _reset_loguru() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


@pytest.fixture(autouse=True)
def _short_preflight_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RALPH_MCP_PREFLIGHT_TIMEOUT_MS", "500")


@contextmanager
def _spawn_fake_sse_mcp() -> Iterator[int]:
    handle = get_process_manager().spawn(
        [sys.executable, "-m", "tests.fixtures.fake_sse_mcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        label="test:fake-sse-mcp-validate",
    )
    try:
        stdout = handle.stdout
        assert stdout is not None
        port_line = stdout.readline().strip()
        if not port_line:
            raise AssertionError("fake_sse_mcp did not print its port before exiting")
        port = int(port_line)
        _wait_for_port(port)
        yield port
    finally:
        with contextlib.suppress(ProcessTerminationError):
            handle.terminate(grace_period_s=5.0)
        if handle.stdout is not None:
            with contextlib.suppress(Exception):
                handle.stdout.close()
        if handle.stderr is not None:
            with contextlib.suppress(Exception):
                handle.stderr.close()


def _wait_for_port(port: int) -> None:
    deadline = time.monotonic() + 5
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.01)
    raise AssertionError(f"fake_sse_mcp never bound port {port}: {last_error}")


def _write_sse_mcp_toml(workspace: Path, port: int) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    body = (
        dedent(
            f"""
        [mcp_servers.fake_sse]
        transport = "http"
        url = "http://127.0.0.1:{port}/sse"
        """
        ).strip()
        + "\n"
    )
    (agent_dir / "mcp.toml").write_text(body, encoding="utf-8")


def test_validate_with_real_sse_fixture_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    with _spawn_fake_sse_mcp() as port:
        _write_sse_mcp_toml(tmp_path, port)
        rc = runner_module._validate_custom_mcp_servers(tmp_path)

    assert rc == 0


def test_validate_with_real_sse_fixture_returns_warning_free_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)
    error_stream = StringIO()
    sink_id = logger.add(error_stream, level="ERROR")
    try:
        with _spawn_fake_sse_mcp() as port:
            _write_sse_mcp_toml(tmp_path, port)
            rc = runner_module._validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(sink_id)

    assert rc == 0
    assert "fake_sse" not in error_stream.getvalue()
