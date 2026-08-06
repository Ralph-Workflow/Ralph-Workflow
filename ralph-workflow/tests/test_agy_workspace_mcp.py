"""Tests for AGY MCP config injection.

The helper mutates two of AGY's global config paths
(``~/.gemini/antigravity-cli/mcp_config.json`` and
``~/.gemini/config/mcp_config.json`` -- see the module docstring in
``ralph.mcp.transport.agy`` for why both are written). Tests patch both
private path helpers so they do not touch the developer's real home
directory.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.transport.agy import agy_workspace_mcp_endpoint

if TYPE_CHECKING:
    from pathlib import Path


def _read_config(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_ralph_only(config: dict[str, object], endpoint: str) -> None:
    servers = config["mcpServers"]
    assert isinstance(servers, dict)
    assert list(servers.keys()) == ["ralph"]
    ralph = servers["ralph"]
    assert isinstance(ralph, dict)
    assert ralph["serverUrl"] == endpoint


@pytest.fixture
def agy_config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Return a temp file standing in for AGY's legacy global MCP config.

    The path is nested (``tmp_path / "gemini" / "mcp_config.json``) so the
    parent-directory creation behaviour is exercised. The secondary path
    (see :func:`agy_secondary_config_path`) is patched to an isolated temp
    file too, so every test that only cares about the legacy path does not
    also touch the developer's real ``~/.gemini/config/mcp_config.json``.
    """
    config_path = tmp_path / "gemini" / "mcp_config.json"
    monkeypatch.setattr("ralph.mcp.transport.agy._agy_global_config_path", lambda: config_path)
    secondary_path = tmp_path / "gemini-config" / "mcp_config.json"
    monkeypatch.setattr(
        "ralph.mcp.transport.agy._agy_secondary_config_path", lambda: secondary_path
    )
    return config_path


def _secondary_path_for(tmp_path: Path) -> Path:
    """Return the secondary-config temp path the ``agy_config_path`` fixture patched in."""
    return tmp_path / "gemini-config" / "mcp_config.json"


def test_agy_workspace_mcp_endpoint_creates_config_when_absent(
    tmp_path: Path, agy_config_path: Path
) -> None:
    endpoint = "http://127.0.0.1:9999/mcp"
    secondary_path = _secondary_path_for(tmp_path)

    assert not agy_config_path.exists()
    assert not secondary_path.exists()

    with agy_workspace_mcp_endpoint(tmp_path, endpoint):
        assert agy_config_path.exists()
        _assert_ralph_only(_read_config(agy_config_path), endpoint)
        # S-2 (Evidence Provenance): the secondary path is what makes live
        # AGY dispatch actually reach the ralph server (module docstring in
        # ralph.mcp.transport.agy) -- it must carry the identical entry.
        assert secondary_path.exists()
        _assert_ralph_only(_read_config(secondary_path), endpoint)

    assert not agy_config_path.exists()
    assert not secondary_path.exists()


def test_agy_workspace_mcp_endpoint_writes_ralph_only_when_existing_servers_present(
    tmp_path: Path, agy_config_path: Path
) -> None:
    original_text = json.dumps(
        {
            "mcpServers": {
                "other-server": {"serverUrl": "http://other.example/mcp"},
            }
        },
        indent=2,
    )
    agy_config_path.parent.mkdir(parents=True, exist_ok=True)
    agy_config_path.write_text(original_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with agy_workspace_mcp_endpoint(tmp_path, endpoint):
        _assert_ralph_only(_read_config(agy_config_path), endpoint)

    assert agy_config_path.read_text(encoding="utf-8") == original_text


def test_agy_workspace_mcp_endpoint_overwrites_stale_ralph_entry(
    tmp_path: Path, agy_config_path: Path
) -> None:
    original_text = json.dumps(
        {
            "mcpServers": {
                "ralph": {"serverUrl": "http://old.example/mcp"},
                "other-server": {"serverUrl": "http://other.example/mcp"},
            }
        },
        indent=2,
    )
    agy_config_path.parent.mkdir(parents=True, exist_ok=True)
    agy_config_path.write_text(original_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with agy_workspace_mcp_endpoint(tmp_path, endpoint):
        _assert_ralph_only(_read_config(agy_config_path), endpoint)

    assert agy_config_path.read_text(encoding="utf-8") == original_text


def test_agy_workspace_mcp_endpoint_restores_on_exception(
    tmp_path: Path, agy_config_path: Path
) -> None:
    original_text = json.dumps(
        {"mcpServers": {"other-server": {"serverUrl": "http://other.example/mcp"}}},
        indent=2,
    )
    agy_config_path.parent.mkdir(parents=True, exist_ok=True)
    agy_config_path.write_text(original_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with pytest.raises(RuntimeError, match="boom"), agy_workspace_mcp_endpoint(tmp_path, endpoint):
        raise RuntimeError("boom")

    assert agy_config_path.read_text(encoding="utf-8") == original_text


def test_agy_workspace_mcp_endpoint_creates_agents_dir_if_missing(
    tmp_path: Path, agy_config_path: Path
) -> None:
    endpoint = "http://127.0.0.1:9999/mcp"

    assert not agy_config_path.parent.exists()

    with agy_workspace_mcp_endpoint(tmp_path, endpoint):
        assert agy_config_path.exists()
        _assert_ralph_only(_read_config(agy_config_path), endpoint)
        assert agy_config_path.parent.exists()

    assert not agy_config_path.exists()


def test_agy_workspace_mcp_endpoint_merges_when_unsafe_mode(
    tmp_path: Path, agy_config_path: Path
) -> None:
    """unsafe_mode=True keeps the existing other-server entry alongside Ralph."""
    original_text = json.dumps(
        {
            "mcpServers": {
                "other-server": {"serverUrl": "http://other.example/mcp"},
            }
        },
        indent=2,
    )
    agy_config_path.parent.mkdir(parents=True, exist_ok=True)
    agy_config_path.write_text(original_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with agy_workspace_mcp_endpoint(tmp_path, endpoint, unsafe_mode=True):
        config = _read_config(agy_config_path)
        servers = config["mcpServers"]
        assert isinstance(servers, dict)
        assert "other-server" in servers
        assert "ralph" in servers
        ralph = servers["ralph"]
        assert isinstance(ralph, dict)
        assert ralph["serverUrl"] == endpoint
        assert "url" not in ralph

    assert agy_config_path.read_text(encoding="utf-8") == original_text


def test_agy_workspace_mcp_endpoint_unsafe_mode_overwrites_stale_ralph(
    tmp_path: Path, agy_config_path: Path
) -> None:
    """unsafe_mode=True replaces a stale ralph entry but keeps other servers."""
    original_text = json.dumps(
        {
            "mcpServers": {
                "ralph": {"serverUrl": "http://old.example/mcp"},
                "other-server": {"serverUrl": "http://other.example/mcp"},
            }
        },
        indent=2,
    )
    agy_config_path.parent.mkdir(parents=True)
    agy_config_path.write_text(original_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with agy_workspace_mcp_endpoint(tmp_path, endpoint, unsafe_mode=True):
        config = _read_config(agy_config_path)
        servers = config["mcpServers"]
        assert isinstance(servers, dict)
        ralph = servers["ralph"]
        assert isinstance(ralph, dict)
        assert ralph["serverUrl"] == endpoint
        assert "other-server" in servers

    assert agy_config_path.read_text(encoding="utf-8") == original_text


def test_agy_workspace_mcp_endpoint_unsafe_mode_merges_workspace_and_global(
    tmp_path: Path, agy_config_path: Path
) -> None:
    """unsafe_mode=True merges both workspace .agents/mcp_config.json and global config."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    agents_dir = workspace / ".agents"
    agents_dir.mkdir()
    workspace_config_file = agents_dir / "mcp_config.json"
    workspace_config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "workspace-http": {"serverUrl": "http://workspace-upstream:7777/mcp"},
                }
            }
        ),
        encoding="utf-8",
    )
    global_text = json.dumps(
        {
            "mcpServers": {
                "global-http": {"serverUrl": "http://global.example/mcp"},
            }
        },
        indent=2,
    )
    agy_config_path.parent.mkdir(parents=True, exist_ok=True)
    agy_config_path.write_text(global_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with agy_workspace_mcp_endpoint(workspace, endpoint, unsafe_mode=True):
        config = _read_config(agy_config_path)
        servers = config["mcpServers"]
        assert isinstance(servers, dict)
        assert "workspace-http" in servers
        assert "global-http" in servers
        assert "ralph" in servers
        ralph = servers["ralph"]
        assert isinstance(ralph, dict)
        assert ralph["serverUrl"] == endpoint
        assert "url" not in ralph
        ws_server = servers["workspace-http"]
        assert ws_server["serverUrl"] == "http://workspace-upstream:7777/mcp"
        global_server = servers["global-http"]
        assert global_server["serverUrl"] == "http://global.example/mcp"

    assert agy_config_path.read_text(encoding="utf-8") == global_text


def test_agy_workspace_mcp_endpoint_serverurl_preserved_on_retained_upstreams(
    tmp_path: Path, agy_config_path: Path
) -> None:
    """Retained upstream entries keep serverUrl key (not url) after unsafe_mode merge."""
    original_text = json.dumps(
        {
            "mcpServers": {
                "other-server": {"serverUrl": "http://other.example/mcp"},
            }
        },
        indent=2,
    )
    agy_config_path.parent.mkdir(parents=True, exist_ok=True)
    agy_config_path.write_text(original_text, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with agy_workspace_mcp_endpoint(tmp_path, endpoint, unsafe_mode=True):
        config = _read_config(agy_config_path)
        servers = config["mcpServers"]
        other = servers["other-server"]
        assert "serverUrl" in other
        assert "url" not in other
        assert other["serverUrl"] == "http://other.example/mcp"


def test_agy_workspace_mcp_endpoint_restores_secondary_path_independently(
    tmp_path: Path, agy_config_path: Path
) -> None:
    """The secondary (live-dispatch) path is staged and restored on its own.

    Gives it a *different* pre-existing entry than the legacy path so a bug
    that restored one path's bytes into the other (rather than each path's
    own captured original) would be caught.
    """
    secondary_path = _secondary_path_for(tmp_path)
    legacy_original = json.dumps(
        {"mcpServers": {"legacy-only": {"serverUrl": "http://legacy.example/mcp"}}},
        indent=2,
    )
    secondary_original = json.dumps(
        {"mcpServers": {"secondary-only": {"serverUrl": "http://secondary.example/mcp"}}},
        indent=2,
    )
    agy_config_path.parent.mkdir(parents=True, exist_ok=True)
    agy_config_path.write_text(legacy_original, encoding="utf-8")
    secondary_path.parent.mkdir(parents=True, exist_ok=True)
    secondary_path.write_text(secondary_original, encoding="utf-8")
    endpoint = "http://127.0.0.1:9999/mcp"

    with agy_workspace_mcp_endpoint(tmp_path, endpoint):
        _assert_ralph_only(_read_config(agy_config_path), endpoint)
        _assert_ralph_only(_read_config(secondary_path), endpoint)

    assert agy_config_path.read_text(encoding="utf-8") == legacy_original
    assert secondary_path.read_text(encoding="utf-8") == secondary_original
