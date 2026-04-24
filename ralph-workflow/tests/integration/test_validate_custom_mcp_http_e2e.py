"""End-to-end HTTP validation of the runner's custom MCP startup probe."""

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

pytestmark = pytest.mark.timeout_seconds(30)


@pytest.fixture(autouse=True)
def _reset_loguru() -> Iterator[None]:
    logger.remove()
    yield
    logger.remove()


@pytest.fixture(autouse=True)
def _short_preflight_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RALPH_MCP_PREFLIGHT_TIMEOUT_MS", "300")


@contextmanager
def _spawn_fake_http_mcp() -> Iterator[int]:
    handle = get_process_manager().spawn(
        [sys.executable, "-m", "tests.fixtures.fake_http_mcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        label="test:fake-http-mcp-validate",
    )
    try:
        stdout = handle.stdout
        assert stdout is not None
        port_line = stdout.readline().strip()
        if not port_line:
            raise AssertionError("fake_http_mcp did not print its port before exiting")
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
    raise AssertionError(f"fake_http_mcp never bound port {port}: {last_error}")


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_http_mcp_toml(workspace: Path, port: int) -> None:
    agent_dir = workspace / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    body = (
        dedent(
            f"""
        [mcp_servers.fake_http]
        transport = "http"
        url = "http://127.0.0.1:{port}/mcp"
        """
        ).strip()
        + "\n"
    )
    (agent_dir / "mcp.toml").write_text(body, encoding="utf-8")


def test_validate_with_real_http_fixture_returns_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)

    with _spawn_fake_http_mcp() as port:
        _write_http_mcp_toml(tmp_path, port)
        rc = runner_module._validate_custom_mcp_servers(tmp_path)

    assert rc == 0


def test_validate_with_unreachable_http_url_returns_one_in_strict_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.delenv("RALPH_MCP_STRICT", raising=False)
    closed_port = _reserve_port()
    _write_http_mcp_toml(tmp_path, closed_port)

    error_stream = StringIO()
    sink_id = logger.add(error_stream, level="ERROR")
    try:
        rc = runner_module._validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(sink_id)

    assert rc == 1
    assert "fake_http" in error_stream.getvalue()


def test_validate_with_unreachable_http_url_returns_zero_in_soft_mode(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))
    monkeypatch.setenv("RALPH_MCP_STRICT", "0")
    closed_port = _reserve_port()
    _write_http_mcp_toml(tmp_path, closed_port)

    warning_stream = StringIO()
    sink_id = logger.add(warning_stream, level="WARNING")
    try:
        rc = runner_module._validate_custom_mcp_servers(tmp_path)
    finally:
        logger.remove(sink_id)

    assert rc == 0
    assert "fake_http" in warning_stream.getvalue()
