"""Tests for ralph/mcp/transport/claude.py."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.transport.claude import (
    claude_mcp_config,
    load_existing_claude_upstream_servers,
    parse_claude_mcp_list_names,
    report_claude_mcp_servers_ralph_cannot_proxy,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    import pytest


def test_claude_mcp_config_produces_http_url_key(tmp_path: Path) -> None:
    """Claude uses url as the HTTP key."""
    endpoint = "http://localhost:8080/mcp"
    config = claude_mcp_config(endpoint)

    parsed = json.loads(config)
    assert "mcpServers" in parsed
    assert "ralph" in parsed["mcpServers"]
    ralph_entry = parsed["mcpServers"]["ralph"]
    assert ralph_entry["type"] == "http"
    assert ralph_entry["url"] == endpoint


def test_load_existing_claude_upstream_servers_returns_empty_when_no_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """When no config file exists, returns empty tuple."""
    monkeypatch.setenv("HOME", str(tmp_path))

    result = load_existing_claude_upstream_servers(workspace_path=None)

    assert result == ()


def test_load_existing_claude_upstream_servers_parses_http_entry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """It parses ~/.claude.json HTTP entries."""
    monkeypatch.setenv("HOME", str(tmp_path))
    config_file = tmp_path / ".claude.json"
    config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "http",
                        "url": "https://api.githubcopilot.com/mcp/",
                    },
                    "filesystem": {
                        "command": "npx",
                        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_existing_claude_upstream_servers(workspace_path=None)

    names = {s.name for s in result}
    assert "github" in names
    assert "filesystem" in names
    http_servers = [s for s in result if s.transport == "http"]
    assert len(http_servers) == 1
    assert http_servers[0].name == "github"
    assert http_servers[0].url == "https://api.githubcopilot.com/mcp/"


def test_load_existing_claude_upstream_servers_reads_workspace_mcp_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """It reads workspace-level .mcp.json."""
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()
    config_file = workspace / ".mcp.json"
    config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ws-upstream": {
                        "type": "http",
                        "url": "http://workspace-upstream:7777/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    names = {s.name for s in result}
    assert "ws-upstream" in names
    http_servers = [s for s in result if s.name == "ws-upstream"]
    assert len(http_servers) == 1
    assert http_servers[0].url == "http://workspace-upstream:7777/mcp"


def test_load_existing_claude_upstream_servers_workspace_overrides_global_on_name_collision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Workspace config overrides global config when names collide."""
    monkeypatch.setenv("HOME", str(tmp_path))
    global_config = tmp_path / ".claude.json"
    global_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared-server": {
                        "type": "http",
                        "url": "https://global.example.invalid/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    workspace = tmp_path / "project"
    workspace.mkdir()
    workspace_config = workspace / ".mcp.json"
    workspace_config.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "shared-server": {
                        "type": "http",
                        "url": "https://workspace.example.invalid/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    assert len(result) == 1
    assert result[0].name == "shared-server"
    assert result[0].url == "https://workspace.example.invalid/mcp"


def test_claude_mcp_config_unsafe_mode_merges_existing_servers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=True merges ~/.claude.json and workspace .mcp.json entries with Ralph."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "http",
                        "url": "https://api.example.com/mcp/",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "workspace-upstream": {
                        "type": "http",
                        "url": "http://workspace-upstream:7777/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = json.loads(
        claude_mcp_config("http://127.0.0.1:9999/mcp", workspace_path=workspace, unsafe_mode=True)
    )

    servers = config["mcpServers"]
    assert "github" in servers
    assert "workspace-upstream" in servers
    assert "ralph" in servers
    ralph = servers["ralph"]
    assert ralph["type"] == "http"
    assert ralph["url"] == "http://127.0.0.1:9999/mcp"


def test_claude_mcp_config_unsafe_mode_false_keeps_ralph_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=False (default) returns a Ralph-only mcpServers payload."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "github": {
                        "type": "http",
                        "url": "https://api.example.com/mcp/",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    config = json.loads(
        claude_mcp_config("http://127.0.0.1:9999/mcp", workspace_path=tmp_path, unsafe_mode=False)
    )

    assert list(config["mcpServers"].keys()) == ["ralph"]


def test_claude_mcp_config_unsafe_mode_overwrites_stale_ralph(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode=True replaces a stale ralph entry but keeps other servers."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ralph": {
                        "type": "http",
                        "url": "http://old.example/mcp",
                    },
                    "github": {
                        "type": "http",
                        "url": "https://api.example.com/mcp/",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    config = json.loads(
        claude_mcp_config("http://127.0.0.1:9999/mcp", workspace_path=tmp_path, unsafe_mode=True)
    )

    servers = config["mcpServers"]
    assert servers["ralph"]["url"] == "http://127.0.0.1:9999/mcp"
    assert servers["github"]["url"] == "https://api.example.com/mcp/"


def test_load_existing_claude_upstream_servers_skips_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Missing ~/.claude.json does not block workspace config loading."""
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()
    config_file = workspace / ".mcp.json"
    config_file.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "workspace-only": {
                        "type": "http",
                        "url": "https://workspace-only.example.invalid/mcp",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    assert len(result) == 1
    assert result[0].name == "workspace-only"
    assert result[0].url == "https://workspace-only.example.invalid/mcp"


def test_claude_transport_regression_stale_ralph_entry_is_dropped_not_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A leftover `ralph` entry in the operator's own config must not abort the load."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "ralph": {
                        "type": "http",
                        "url": "http://stale.example/mcp",
                    },
                    "github": {
                        "type": "http",
                        "url": "https://api.example.com/mcp/",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    result = load_existing_claude_upstream_servers(workspace_path=None)

    assert [server.name for server in result] == ["github"]
    assert result[0].url == "https://api.example.com/mcp/"


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_claude_transport_regression_plugin_provided_servers_are_discovered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """An enabled plugin's `.mcp.json` servers must survive `--strict-mcp-config`.

    Claude Code loads `<plugin-root>/.mcp.json` for every enabled plugin and
    exposes them as `plugin:<plugin>:<server>`. Ralph strips every non-Ralph
    MCP source from the CLI, so a plugin server it never discovered is simply
    deleted from the operator's world -- the OpenCode `--pure` defect again.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    install_path = tmp_path / ".claude" / "plugins" / "cache" / "mkt" / "ui-pro" / "2.13.0"
    _write_json(
        tmp_path / ".claude" / "settings.json",
        {"enabledPlugins": {"ui-pro@mkt": True}},
    )
    _write_json(
        tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"ui-pro@mkt": [{"installPath": str(install_path)}]}},
    )
    _write_json(
        install_path / ".mcp.json",
        {"mcpServers": {"shadcn": {"command": "npx", "args": ["-y", "shadcn@latest", "mcp"]}}},
    )

    result = load_existing_claude_upstream_servers(workspace_path=None)

    assert [server.name for server in result] == ["plugin_ui-pro_shadcn"]
    assert result[0].transport == "stdio"
    assert result[0].command == "npx"
    assert result[0].args == ("-y", "shadcn@latest", "mcp")


def test_claude_transport_regression_disabled_plugin_servers_stay_undiscovered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A plugin the operator disabled must not be re-exposed as a proxied upstream."""
    monkeypatch.setenv("HOME", str(tmp_path))
    install_path = tmp_path / ".claude" / "plugins" / "cache" / "mkt" / "ui-pro" / "2.13.0"
    _write_json(
        tmp_path / ".claude" / "settings.json",
        {"enabledPlugins": {"ui-pro@mkt": False}},
    )
    _write_json(
        tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"ui-pro@mkt": [{"installPath": str(install_path)}]}},
    )
    _write_json(install_path / ".mcp.json", {"mcpServers": {"shadcn": {"command": "npx"}}})

    result = load_existing_claude_upstream_servers(workspace_path=None)

    assert result == ()


def test_claude_transport_regression_plugin_server_does_not_shadow_user_server(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A plugin server sharing a user server's name must not delete either of them."""
    monkeypatch.setenv("HOME", str(tmp_path))
    install_path = tmp_path / ".claude" / "plugins" / "cache" / "mkt" / "ui-pro" / "2.13.0"
    _write_json(
        tmp_path / ".claude" / "settings.json",
        {"enabledPlugins": {"ui-pro@mkt": True}},
    )
    _write_json(
        tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"ui-pro@mkt": [{"installPath": str(install_path)}]}},
    )
    _write_json(
        install_path / ".mcp.json",
        {"mcpServers": {"playwright": {"command": "npx", "args": ["-y", "@playwright/mcp"]}}},
    )
    _write_json(
        tmp_path / ".claude.json",
        {"mcpServers": {"playwright": {"type": "http", "url": "https://user.example/mcp"}}},
    )

    result = load_existing_claude_upstream_servers(workspace_path=None)

    by_name = {server.name: server for server in result}
    assert set(by_name) == {"plugin_ui-pro_playwright", "playwright"}
    assert by_name["playwright"].url == "https://user.example/mcp"
    assert by_name["plugin_ui-pro_playwright"].command == "npx"


def test_claude_transport_regression_local_scope_project_servers_are_discovered(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """`claude mcp add -s local` writes ~/.claude.json projects.<cwd>.mcpServers.

    That is the default scope of `claude mcp add`, so it is the most likely
    place an operator's server lives, and Ralph never looked at it.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_json(
        tmp_path / ".claude.json",
        {
            "mcpServers": {"user-scope": {"type": "http", "url": "https://user.example/mcp"}},
            "projects": {
                str(workspace): {
                    "mcpServers": {
                        "local-scope": {
                            "type": "stdio",
                            "command": "/bin/echo",
                            "args": ["hi"],
                        }
                    }
                },
                str(tmp_path / "other"): {
                    "mcpServers": {"other-project": {"command": "/bin/false"}}
                },
            },
        },
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    assert {server.name for server in result} == {"user-scope", "local-scope"}


def test_claude_transport_regression_local_scope_outranks_project_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Claude resolves local scope above project scope; Ralph's proxy must agree."""
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_json(
        workspace / ".mcp.json",
        {"mcpServers": {"shared": {"type": "http", "url": "https://project.example/mcp"}}},
    )
    _write_json(
        tmp_path / ".claude.json",
        {
            "projects": {
                str(workspace): {
                    "mcpServers": {"shared": {"type": "http", "url": "https://local.example/mcp"}}
                }
            }
        },
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    assert [server.name for server in result] == ["shared"]
    assert result[0].url == "https://local.example/mcp"


def test_claude_transport_regression_unreadable_config_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A corrupt config must warn loudly instead of yielding a silent empty set."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".claude.json").write_text("{ not json", encoding="utf-8")
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        result = load_existing_claude_upstream_servers(workspace_path=None)
    finally:
        logger.remove(sink_id)

    assert result == ()
    warning = "\n".join(records)
    assert str(tmp_path / ".claude.json") in warning


def test_claude_transport_regression_plugin_named_ralph_server_is_dropped_not_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The reserved `ralph` name must still be dropped before normalization."""
    monkeypatch.setenv("HOME", str(tmp_path))
    install_path = tmp_path / ".claude" / "plugins" / "cache" / "mkt" / "ui-pro" / "2.13.0"
    _write_json(
        tmp_path / ".claude" / "settings.json",
        {"enabledPlugins": {"ui-pro@mkt": True}},
    )
    _write_json(
        tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"ui-pro@mkt": [{"installPath": str(install_path)}]}},
    )
    _write_json(install_path / ".mcp.json", {"mcpServers": {"keep-me": {"command": "npx"}}})
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_json(
        tmp_path / ".claude.json",
        {
            "mcpServers": {"ralph": {"type": "http", "url": "http://stale.example/mcp"}},
            "projects": {
                str(workspace): {
                    "mcpServers": {"ralph": {"type": "http", "url": "http://stale2.example/mcp"}}
                }
            },
        },
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    assert [server.name for server in result] == ["plugin_ui-pro_keep-me"]


def test_claude_transport_regression_unsafe_mode_keeps_plugin_and_local_scope_servers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """unsafe_mode must see every source the strict-mode loader sees.

    The unsafe-mode merge used to re-implement Claude discovery from the two
    plain ``mcpServers`` files only, so an operator who ran Ralph in unsafe
    mode still lost their plugin-provided and ``claude mcp add`` (local scope)
    servers -- the very servers unsafe mode exists to preserve.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()
    install_path = tmp_path / ".claude" / "plugins" / "cache" / "mkt" / "ui-pro" / "2.13.0"
    _write_json(
        tmp_path / ".claude" / "settings.json",
        {"enabledPlugins": {"ui-pro@mkt": True}},
    )
    _write_json(
        tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"ui-pro@mkt": [{"installPath": str(install_path)}]}},
    )
    _write_json(install_path / ".mcp.json", {"mcpServers": {"shadcn": {"command": "npx"}}})
    _write_json(
        tmp_path / ".claude.json",
        {
            "mcpServers": {"user-scope": {"type": "http", "url": "https://user.example/mcp"}},
            "projects": {
                str(workspace): {
                    "mcpServers": {"local-scope": {"command": "/bin/echo", "args": ["hi"]}}
                }
            },
        },
    )

    config = json.loads(
        claude_mcp_config("http://127.0.0.1:9999/mcp", workspace_path=workspace, unsafe_mode=True)
    )

    assert set(config["mcpServers"]) == {
        "ralph",
        "plugin_ui-pro_shadcn",
        "user-scope",
        "local-scope",
    }


def test_claude_transport_regression_disabled_mcpjson_server_is_not_reexposed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A project server the operator rejected must not come back as a Ralph proxy.

    ``projects.<cwd>.disabledMcpjsonServers`` in ``~/.claude.json`` records the
    ``.mcp.json`` servers the operator explicitly declined. Ralph proxied them
    anyway, overriding that choice.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_json(
        workspace / ".mcp.json",
        {
            "mcpServers": {
                "declined": {"type": "http", "url": "https://declined.example/mcp"},
                "approved": {"type": "http", "url": "https://approved.example/mcp"},
            }
        },
    )
    _write_json(
        tmp_path / ".claude.json",
        {"projects": {str(workspace): {"disabledMcpjsonServers": ["declined"]}}},
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    assert [server.name for server in result] == ["approved"]


def test_claude_transport_regression_disabled_project_server_keeps_user_scope_twin(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Disabling a project server must not delete a same-named user-scope server.

    ``disabledMcpjsonServers`` names ``.mcp.json`` entries only. A naive
    post-merge filter would drop the user-scope server that shares the name and
    that Claude itself still runs.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    workspace = tmp_path / "project"
    workspace.mkdir()
    _write_json(
        workspace / ".mcp.json",
        {"mcpServers": {"shared": {"type": "http", "url": "https://project.example/mcp"}}},
    )
    _write_json(
        tmp_path / ".claude.json",
        {
            "mcpServers": {"shared": {"type": "http", "url": "https://user.example/mcp"}},
            "projects": {str(workspace): {"disabledMcpjsonServers": ["shared"]}},
        },
    )

    result = load_existing_claude_upstream_servers(workspace_path=workspace)

    assert [server.name for server in result] == ["shared"]
    assert result[0].url == "https://user.example/mcp"


def _fixed_cli_lister(names: tuple[str, ...] | None) -> Callable[[], tuple[str, ...] | None]:
    def _lister() -> tuple[str, ...] | None:
        return names

    return _lister


def test_claude_transport_regression_account_connectors_are_reported_by_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Servers Ralph strips but cannot re-proxy must be named to the operator.

    claude.ai account connectors are delivered by the signed-in account and
    exist in no file, so ``--strict-mcp-config`` deletes them for the run and
    Ralph has nothing to proxy back. That used to happen silently.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _write_json(
        tmp_path / ".claude.json",
        {"mcpServers": {"docs-mcp-server": {"command": "docs-mcp-server"}}},
    )
    monkeypatch.setattr(
        "ralph.mcp.transport.claude.claude_cli_mcp_server_lister",
        _fixed_cli_lister(("claude.ai Notion", "claude.ai Gmail", "docs-mcp-server")),
    )
    report_claude_mcp_servers_ralph_cannot_proxy.cache_clear()

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        missing = report_claude_mcp_servers_ralph_cannot_proxy(None)
    finally:
        logger.remove(sink_id)

    assert missing == ("claude.ai Notion", "claude.ai Gmail")
    warning = "\n".join(records)
    assert "claude.ai Notion" in warning
    assert "claude.ai Gmail" in warning
    assert "docs-mcp-server" not in warning


def test_claude_transport_regression_unproxyable_report_is_emitted_once_per_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The report is a per-run notice, not a per-invocation subprocess spawn."""
    monkeypatch.setenv("HOME", str(tmp_path))
    calls: list[int] = []

    def _counting_lister() -> tuple[str, ...] | None:
        calls.append(1)
        return ("claude.ai Notion",)

    monkeypatch.setattr(
        "ralph.mcp.transport.claude.claude_cli_mcp_server_lister", _counting_lister
    )
    report_claude_mcp_servers_ralph_cannot_proxy.cache_clear()

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        first = report_claude_mcp_servers_ralph_cannot_proxy(None)
        second = report_claude_mcp_servers_ralph_cannot_proxy(None)
    finally:
        logger.remove(sink_id)

    assert first == ("claude.ai Notion",)
    assert second == ("claude.ai Notion",)
    assert len(calls) == 1
    assert len(records) == 1


def test_claude_transport_regression_unavailable_claude_cli_does_not_break_the_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A missing, slow, or failing `claude mcp list` must not fail the run."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(
        "ralph.mcp.transport.claude.claude_cli_mcp_server_lister", _fixed_cli_lister(None)
    )
    report_claude_mcp_servers_ralph_cannot_proxy.cache_clear()

    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        missing = report_claude_mcp_servers_ralph_cannot_proxy(None)
    finally:
        logger.remove(sink_id)

    assert missing == ()
    assert records == []


def test_claude_transport_regression_plugin_servers_are_not_reported_as_lost(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Claude spells a plugin server `plugin:<plugin>:<server>`; Ralph uses `_`.

    Comparing the two spellings literally would report every plugin server as
    lost on every run and bury the connectors that really are lost.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    install_path = tmp_path / ".claude" / "plugins" / "cache" / "mkt" / "ui-pro" / "2.13.0"
    _write_json(
        tmp_path / ".claude" / "settings.json",
        {"enabledPlugins": {"ui-pro@mkt": True}},
    )
    _write_json(
        tmp_path / ".claude" / "plugins" / "installed_plugins.json",
        {"version": 2, "plugins": {"ui-pro@mkt": [{"installPath": str(install_path)}]}},
    )
    _write_json(install_path / ".mcp.json", {"mcpServers": {"shadcn": {"command": "npx"}}})
    monkeypatch.setattr(
        "ralph.mcp.transport.claude.claude_cli_mcp_server_lister",
        _fixed_cli_lister(("plugin:ui-pro:shadcn", "claude.ai Gmail")),
    )
    report_claude_mcp_servers_ralph_cannot_proxy.cache_clear()

    missing = report_claude_mcp_servers_ralph_cannot_proxy(None)

    assert missing == ("claude.ai Gmail",)


def test_claude_transport_regression_mcp_list_output_parses_every_server_name() -> None:
    """`claude mcp list` names are read from its real output shape."""
    output = (
        "Checking MCP server health…\n"
        "\n"
        "claude.ai Notion: https://mcp.notion.com/mcp - ! Needs authentication\n"
        "claude.ai Gmail: https://gmailmcp.googleapis.com/mcp/v1 - ✔ Connected\n"
        "angular: npx -y @angular/cli mcp - ✔ Connected\n"
        "docs-mcp-server: /usr/local/bin/docs-mcp-server  - ✔ Connected\n"
    )

    assert parse_claude_mcp_list_names(output) == (
        "claude.ai Notion",
        "claude.ai Gmail",
        "angular",
        "docs-mcp-server",
    )
