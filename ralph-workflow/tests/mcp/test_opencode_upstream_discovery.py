"""Tests for OpenCode operator-config discovery in ralph/mcp/transport/opencode.py.

OpenCode was the only supported transport with no
``load_existing_<agent>_upstream_servers``: Ralph seeded
``build_opencode_provider_config`` from ``OPENCODE_CONFIG_CONTENT`` alone,
which is normally unset, so Ralph never learned about the MCP servers the
operator declared in their own OpenCode config files.

The config sources asserted here were read out of the installed OpenCode
1.18.25 binary (``Config.loadInstanceState`` / ``ConfigPaths``) and confirmed
against ``opencode debug config``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from loguru import logger

from ralph.mcp.transport.opencode import (
    build_opencode_provider_config,
    load_existing_opencode_upstream_servers,
    report_opencode_mcp_servers_outside_ralph_gate,
    reset_opencode_mcp_gate_report,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_ENDPOINT = "http://127.0.0.1:9999/mcp"


def _isolate_opencode_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point every OpenCode config location at ``tmp_path`` and return the global dir."""
    xdg_config_home = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config_home))
    monkeypatch.setenv("OPENCODE_TEST_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENCODE_TEST_MANAGED_CONFIG_DIR", str(tmp_path / "managed"))
    monkeypatch.delenv("OPENCODE_CONFIG", raising=False)
    monkeypatch.delenv("OPENCODE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("OPENCODE_DISABLE_PROJECT_CONFIG", raising=False)
    reset_opencode_mcp_gate_report()
    global_dir = xdg_config_home / "opencode"
    global_dir.mkdir(parents=True)
    return global_dir


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _mcp(**servers: object) -> dict[str, object]:
    return {"$schema": "https://opencode.ai/config.json", "mcp": dict(servers)}


_REMOTE = {"type": "remote", "url": "https://ctx.example/mcp", "enabled": True}


def test_opencode_transport_regression_operator_global_config_is_discovered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The operator's global ``opencode.json`` must be discovered, not ignored."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    _write_json(global_dir / "opencode.json", _mcp(context7=_REMOTE))

    servers = load_existing_opencode_upstream_servers(None)

    assert [server.name for server in servers] == ["context7"]
    assert servers[0].transport == "http"
    assert servers[0].url == "https://ctx.example/mcp"


def test_opencode_transport_regression_local_server_command_list_is_normalized(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """OpenCode spells a stdio command as a LIST; a str-only reader dropped the server."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    _write_json(
        global_dir / "opencode.json",
        _mcp(
            angular={
                "type": "local",
                "command": ["bunx", "@angular/cli", "mcp"],
                "environment": {"NG_TOKEN": "x"},
                "enabled": True,
            }
        ),
    )

    servers = load_existing_opencode_upstream_servers(None)

    assert [server.name for server in servers] == ["angular"]
    assert servers[0].transport == "stdio"
    assert servers[0].command == "bunx"
    assert servers[0].args == ("@angular/cli", "mcp")
    assert servers[0].env == {"NG_TOKEN": "x"}


def test_opencode_transport_regression_every_config_location_is_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every location OpenCode itself loads must be read, not just the global file."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    workspace = tmp_path / "repo"
    (workspace / ".git").mkdir(parents=True)
    custom_config = tmp_path / "custom" / "mycfg.json"
    config_dir = tmp_path / "cfgdir"
    monkeypatch.setenv("OPENCODE_CONFIG", str(custom_config))
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(config_dir))

    _write_json(global_dir / "config.json", _mcp(legacy=_REMOTE))
    _write_json(global_dir / "opencode.json", _mcp(globalsrv=_REMOTE))
    _write_json(custom_config, _mcp(customenv=_REMOTE))
    _write_json(workspace / "opencode.json", _mcp(projsrv=_REMOTE))
    _write_json(workspace / ".opencode" / "opencode.json", _mcp(dotdir=_REMOTE))
    _write_json(tmp_path / "home" / ".opencode" / "opencode.json", _mcp(homedot=_REMOTE))
    _write_json(config_dir / "opencode.json", _mcp(cfgdir=_REMOTE))
    _write_json(tmp_path / "managed" / "opencode.json", _mcp(managed=_REMOTE))

    names = {server.name for server in load_existing_opencode_upstream_servers(workspace)}

    assert names == {
        "legacy",
        "globalsrv",
        "customenv",
        "projsrv",
        "dotdir",
        "homedot",
        "cfgdir",
        "managed",
    }


def test_opencode_transport_regression_project_config_wins_over_global(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Precedence must match OpenCode's deep merge: the project file wins."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    workspace = tmp_path / "repo"
    (workspace / ".git").mkdir(parents=True)
    _write_json(
        global_dir / "opencode.json",
        _mcp(shared={"type": "remote", "url": "https://global.example/mcp", "enabled": True}),
    )
    _write_json(
        workspace / "opencode.json",
        _mcp(shared={"type": "remote", "url": "https://project.example/mcp", "enabled": True}),
    )

    servers = load_existing_opencode_upstream_servers(workspace)

    assert [server.url for server in servers] == ["https://project.example/mcp"]


def test_opencode_transport_regression_jsonc_config_is_not_silently_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``opencode.jsonc`` is a first-class config name; comments must not lose it."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    (global_dir / "opencode.jsonc").write_text(
        '// operator notes\n{\n  "mcp": {\n    /* block */\n'
        '    "refero": {"type": "remote", "url": "https://refero.example/mcp"},\n  }\n}\n',
        encoding="utf-8",
    )

    servers = load_existing_opencode_upstream_servers(None)

    assert [server.name for server in servers] == ["refero"]


def test_opencode_transport_regression_disabled_server_is_not_discovered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A server the operator switched off must not be reported as available."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    _write_json(
        global_dir / "opencode.json",
        _mcp(
            off={"type": "remote", "url": "https://off.example/mcp", "enabled": False},
            also_off={"type": "remote", "url": "https://off2.example/mcp", "disabled": True},
            on=_REMOTE,
        ),
    )

    servers = load_existing_opencode_upstream_servers(None)

    assert [server.name for server in servers] == ["on"]


def test_opencode_transport_regression_project_config_opt_out_is_honoured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """``OPENCODE_DISABLE_PROJECT_CONFIG`` drops project files AND project ``.opencode``."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    monkeypatch.setenv("OPENCODE_DISABLE_PROJECT_CONFIG", "true")
    workspace = tmp_path / "repo"
    (workspace / ".git").mkdir(parents=True)
    _write_json(global_dir / "opencode.json", _mcp(globalsrv=_REMOTE))
    _write_json(workspace / "opencode.json", _mcp(projsrv=_REMOTE))
    _write_json(workspace / ".opencode" / "opencode.json", _mcp(dotdir=_REMOTE))

    names = {server.name for server in load_existing_opencode_upstream_servers(workspace)}

    assert names == {"globalsrv"}


def test_opencode_transport_regression_reserved_ralph_name_does_not_abort_discovery(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale operator-side `ralph` entry must be dropped, never raise."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    _write_json(
        global_dir / "opencode.json",
        _mcp(
            ralph={"type": "remote", "url": "http://stale.example/mcp", "enabled": True},
            context7=_REMOTE,
        ),
    )

    servers = load_existing_opencode_upstream_servers(None)

    assert [server.name for server in servers] == ["context7"]


def test_opencode_transport_regression_unreadable_config_is_reported_not_swallowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A corrupt config must warn loudly instead of yielding a silent empty set."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    broken = global_dir / "opencode.json"
    broken.write_text("{ not json", encoding="utf-8")
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        servers = load_existing_opencode_upstream_servers(None)
    finally:
        logger.remove(sink_id)

    assert servers == ()
    assert str(broken) in "\n".join(records)


def test_opencode_transport_regression_discovery_runs_without_config_content_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Restricted mode must find the operator's servers with OPENCODE_CONFIG_CONTENT unset."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    _write_json(global_dir / "opencode.json", _mcp(context7=_REMOTE))
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        build_opencode_provider_config(None, _ENDPOINT)
    finally:
        logger.remove(sink_id)

    assert "context7" in "\n".join(records)


def test_opencode_transport_regression_discovered_servers_are_not_double_exposed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """OpenCode deep-merges its config sources, so a proxy copy would duplicate every tool."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    _write_json(global_dir / "opencode.json", _mcp(context7=_REMOTE))

    for unsafe_mode in (False, True):
        reset_opencode_mcp_gate_report()
        config_text, upstreams = build_opencode_provider_config(
            None, _ENDPOINT, unsafe_mode=unsafe_mode
        )

        # Never proxied: the operator's server is still reachable natively.
        assert [server.name for server in upstreams] == []
        # Never written into the provider config either: Ralph must not
        # restate a server OpenCode's own merge already delivers.
        assert list(json.loads(config_text)["mcp"]) == ["ralph"]


def test_opencode_transport_regression_gate_report_warns_once_per_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The out-of-gate notice is a once-per-run operator notice, not per-cycle spam."""
    global_dir = _isolate_opencode_env(monkeypatch, tmp_path)
    _write_json(global_dir / "opencode.json", _mcp(context7=_REMOTE, refero=_REMOTE))
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        first = report_opencode_mcp_servers_outside_ralph_gate(None)
        second = report_opencode_mcp_servers_outside_ralph_gate(None)
    finally:
        logger.remove(sink_id)

    assert first == ("context7", "refero")
    assert second == first
    assert len(records) == 1


def test_opencode_transport_regression_no_operator_servers_emits_no_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An operator with no MCP servers must not be warned about servers they do not have."""
    _isolate_opencode_env(monkeypatch, tmp_path)
    records: list[str] = []
    sink_id = logger.add(records.append, level="WARNING", format="{message}")
    try:
        missing = report_opencode_mcp_servers_outside_ralph_gate(None)
    finally:
        logger.remove(sink_id)

    assert missing == ()
    assert records == []
