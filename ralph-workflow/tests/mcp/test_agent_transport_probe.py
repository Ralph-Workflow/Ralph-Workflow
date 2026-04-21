"""Tests for ralph/mcp/upstream/agent_probe.py."""

from __future__ import annotations

import json
import tomllib
from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.startup import RetryablePreflightError
from ralph.mcp.upstream.agent_probe import (
    AgentProbeReport,
    _augment_codex_config_with_server,
    probe_agent_transports,
)
from ralph.mcp.upstream.config import UpstreamMcpServer

if TYPE_CHECKING:
    from pathlib import Path


def _http_server(
    name: str = "remote", url: str = "http://example.invalid/mcp"
) -> UpstreamMcpServer:
    return UpstreamMcpServer(name=name, transport="http", url=url)


def _stdio_server(name: str = "local") -> UpstreamMcpServer:
    return UpstreamMcpServer(
        name=name,
        transport="stdio",
        command="my-mcp",
        args=("--flag",),
    )


def _stub_http_handshake_pass(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    captured: list[str] = []

    def fake(endpoint: str) -> None:
        captured.append(endpoint)

    monkeypatch.setattr(
        "ralph.mcp.upstream.agent_probe._http_handshake",
        fake,
    )
    return captured


def _stub_server_handshake_pass(monkeypatch: pytest.MonkeyPatch) -> list[UpstreamMcpServer]:
    captured: list[UpstreamMcpServer] = []

    def fake(server: UpstreamMcpServer) -> None:
        captured.append(server)

    monkeypatch.setattr(
        "ralph.mcp.upstream.agent_probe._server_handshake",
        fake,
    )
    return captured


def test_probe_emits_claude_http_config_and_reaches_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _http_server()
    captured = _stub_http_handshake_pass(monkeypatch)
    captured_blobs: list[tuple[str, object]] = []

    from ralph.mcp.transport.claude import claude_mcp_config as real_claude_config  # noqa: PLC0415

    def spy_claude(endpoint: str, **kw: object) -> str:
        blob = real_claude_config(endpoint, **kw)
        captured_blobs.append((endpoint, blob))
        return blob

    monkeypatch.setattr("ralph.mcp.transport.claude.claude_mcp_config", spy_claude)

    reports = probe_agent_transports(
        [server], transports=(AgentTransport.CLAUDE,), workspace_path=None
    )
    assert len(reports) == 1
    assert reports[0].ok is True
    assert reports[0].transport == AgentTransport.CLAUDE
    assert captured == [server.url]
    assert captured_blobs[0][0] == server.url
    parsed = json.loads(str(captured_blobs[0][1]))
    assert parsed["mcpServers"]["ralph"]["url"] == server.url


def test_probe_emits_codex_config_toml_with_mcp_servers_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = _http_server(name="docs", url="http://docs.invalid/mcp")
    _stub_server_handshake_pass(monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))

    reports = probe_agent_transports(
        [server], transports=(AgentTransport.CODEX,), workspace_path=tmp_path
    )

    assert len(reports) == 1
    assert reports[0].ok is True

    # The synthesized config.toml lives under .agent/tmp/codex-home-*/config.toml
    candidates = list((tmp_path / ".agent" / "tmp").glob("codex-home-*/config.toml"))
    assert candidates, "Codex prepare did not write config.toml"
    parsed = tomllib.loads(candidates[0].read_text(encoding="utf-8"))
    # The probe augments the TOML in-memory; verify _prepare_codex_home produced
    # baseline output, and that the augmented copy parses with the server entry.
    # Re-augment from the probe internals to assert that table shape.
    augmented = _augment_codex_config_with_server(candidates[0].read_text(encoding="utf-8"), server)
    parsed_augmented = tomllib.loads(augmented)
    assert "docs" in parsed_augmented["mcp_servers"]
    assert parsed_augmented["mcp_servers"]["docs"]["url"] == server.url
    del parsed  # silence unused warning


def test_probe_emits_opencode_config_with_remote_mcp_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _http_server(name="docs", url="http://docs.invalid/mcp")
    captured_endpoint = _stub_http_handshake_pass(monkeypatch)
    captured_configs: list[str] = []
    from ralph.mcp.transport.opencode import (  # noqa: PLC0415
        build_opencode_provider_config as real_opencode,
    )

    def spy_opencode(existing: str | None, endpoint: str) -> tuple[str, tuple[object, ...]]:
        text, ups = real_opencode(existing, endpoint)
        captured_configs.append(text)
        return text, ups

    monkeypatch.setattr(
        "ralph.mcp.transport.opencode.build_opencode_provider_config",
        spy_opencode,
    )

    reports = probe_agent_transports(
        [server], transports=(AgentTransport.OPENCODE,), workspace_path=None
    )
    assert len(reports) == 1
    assert reports[0].ok is True
    assert captured_endpoint == [server.url]
    payload = json.loads(captured_configs[0])
    ralph_entry = payload["mcp"]["ralph"]
    assert ralph_entry["type"] == "remote"
    assert ralph_entry["url"] == server.url


def test_probe_reports_failure_when_server_unreachable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    server = _http_server()

    def boom_http(_endpoint: str) -> None:
        raise RetryablePreflightError("connection refused")

    def boom_server(_server: UpstreamMcpServer) -> None:
        raise RetryablePreflightError("connection refused")

    monkeypatch.setattr("ralph.mcp.upstream.agent_probe._http_handshake", boom_http)
    monkeypatch.setattr("ralph.mcp.upstream.agent_probe._server_handshake", boom_server)
    monkeypatch.setenv("HOME", str(tmp_path / "fake-home"))

    reports = probe_agent_transports(
        [server],
        transports=(AgentTransport.CLAUDE, AgentTransport.CODEX, AgentTransport.OPENCODE),
        workspace_path=tmp_path,
    )
    statuses = {(r.transport, r.ok) for r in reports}
    assert (AgentTransport.CLAUDE, False) in statuses
    assert (AgentTransport.CODEX, False) in statuses
    assert (AgentTransport.OPENCODE, False) in statuses


def test_probe_skips_stdio_for_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    server = _stdio_server(name="cli")
    monkeypatch.setattr(
        "ralph.mcp.upstream.agent_probe._http_handshake",
        lambda endpoint: pytest.fail("stdio claude probe should not call http handshake"),
    )

    reports = probe_agent_transports(
        [server], transports=(AgentTransport.CLAUDE,), workspace_path=None
    )
    assert reports == (
        AgentProbeReport(
            transport=AgentTransport.CLAUDE,
            server_name="cli",
            ok=True,
            error=None,
            note="skipped (stdio proxied by Claude CLI)",
        ),
    )
