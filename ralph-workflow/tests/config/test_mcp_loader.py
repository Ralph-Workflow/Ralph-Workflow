"""Tests for the three-layer mcp.toml loader (T3).

Merge order under test:
  bundled default (empty) -> user-global -> project-local
"""

from __future__ import annotations

import textwrap
import tomllib
from typing import TYPE_CHECKING

import pytest
from loguru import logger

from ralph.config.mcp_loader import (
    _bundled_default_mcp_config_path,
    _global_mcp_config_path,
    _local_mcp_config_path,
    load_mcp_config,
)
from ralph.config.mcp_models import McpConfig

if TYPE_CHECKING:
    from pathlib import Path


DEFAULT_MAX_INLINE_BYTES = 5_242_880  # 5 MiB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeScope:
    """Minimal WorkspaceScope stand-in for loader tests."""

    def __init__(self, root: Path) -> None:
        self.local_config_path = root / ".agent" / "ralph-workflow.toml"


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------


def test_bundled_default_path_resolves_to_existing_file() -> None:
    path = _bundled_default_mcp_config_path()
    assert path.exists(), f"Bundled default mcp.toml not found at {path}"
    assert path.name == "mcp.toml"


def test_bundled_default_parses_as_empty_dict() -> None:
    path = _bundled_default_mcp_config_path()
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    assert data == {}, "Bundled default must be all comments; no active TOML keys"


def test_global_mcp_config_path_default_location(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    path = _global_mcp_config_path()
    assert path.name == "ralph-workflow-mcp.toml"
    assert ".config" in str(path)


def test_global_mcp_config_path_respects_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = _global_mcp_config_path()
    assert path == tmp_path / "ralph-workflow-mcp.toml"


def test_local_mcp_config_path(tmp_path: Path) -> None:
    scope = _FakeScope(tmp_path)
    path = _local_mcp_config_path(scope)  # type: ignore[arg-type]
    assert path == tmp_path / ".agent" / "mcp.toml"


# ---------------------------------------------------------------------------
# No files present -> defaults
# ---------------------------------------------------------------------------


def test_load_mcp_config_no_files_returns_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    config = load_mcp_config(config_path=tmp_path / "nonexistent.toml")

    assert isinstance(config, McpConfig)
    assert config.mcp_servers == {}
    assert config.web_search.enabled is True
    assert config.web_search.backend == "ddgs"


# ---------------------------------------------------------------------------
# Layer merge order
# ---------------------------------------------------------------------------


def test_load_mcp_config_local_overrides_global(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Project-local backend setting overrides user-global backend setting."""
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    (xdg / "ralph-workflow-mcp.toml").write_text(
        textwrap.dedent("""\
            [web_search]
            backend = "searxng"
        """),
        encoding="utf-8",
    )
    local_file = tmp_path / "mcp.toml"
    local_file.write_text(
        textwrap.dedent("""\
            [web_search]
            backend = "ddgs"
        """),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    config = load_mcp_config(config_path=local_file)
    assert config.web_search.backend == "ddgs", "local layer must win over global"


def test_load_mcp_config_global_servers_survive_local_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Servers declared in user-global appear even when a local file exists."""
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    (xdg / "ralph-workflow-mcp.toml").write_text(
        textwrap.dedent("""\
            [mcp_servers.docs]
            name = "docs"
            transport = "http"
            url = "https://mcp.example.com/docs"
        """),
        encoding="utf-8",
    )
    local_file = tmp_path / "mcp.toml"
    local_file.write_text(
        textwrap.dedent("""\
            [web_search]
            enabled = false
        """),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    config = load_mcp_config(config_path=local_file)
    assert "docs" in config.mcp_servers, "global server must survive local override"
    assert config.web_search.enabled is False, "local flag must be applied"


def test_load_mcp_config_workspace_scope_local_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When workspace_scope is provided (no config_path), derive local from scope."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "mcp.toml").write_text(
        textwrap.dedent("""\
            [web_search]
            enabled = false
        """),
        encoding="utf-8",
    )
    scope = _FakeScope(tmp_path)
    config = load_mcp_config(workspace_scope=scope)  # type: ignore[arg-type]
    assert config.web_search.enabled is False


# ---------------------------------------------------------------------------
# Fail-fast TOML parse errors
# ---------------------------------------------------------------------------


def test_load_mcp_config_malformed_local_toml_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    bad = tmp_path / "bad.toml"
    bad.write_bytes(b"[invalid = }")
    with pytest.raises(SystemExit) as exc_info:
        load_mcp_config(config_path=bad)
    assert exc_info.value.code == 1


def test_load_mcp_config_malformed_global_toml_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    (xdg / "ralph-workflow-mcp.toml").write_bytes(b"not_valid = }")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    with pytest.raises(SystemExit) as exc_info:
        load_mcp_config()
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Schema validation errors
# ---------------------------------------------------------------------------


def test_load_mcp_config_reserved_server_name_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    bad = tmp_path / "mcp.toml"
    bad.write_text(
        textwrap.dedent("""\
            [mcp_servers.ralph]
            name = "ralph"
            transport = "stdio"
            command = "npx"
        """),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc_info:
        load_mcp_config(config_path=bad)
    assert exc_info.value.code == 1


# ---------------------------------------------------------------------------
# Fallback backend validation
# ---------------------------------------------------------------------------


def test_load_mcp_config_unknown_fallback_backend_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = tmp_path / "mcp.toml"
    cfg.write_text(
        textwrap.dedent("""\
            [web_search]
            fallback = ["phantom_engine"]
        """),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc_info:
        load_mcp_config(config_path=cfg)
    assert exc_info.value.code == 1


def test_load_mcp_config_ddgs_in_fallback_always_allowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = tmp_path / "mcp.toml"
    cfg.write_text(
        textwrap.dedent("""\
            [web_search]
            fallback = ["ddgs"]
        """),
        encoding="utf-8",
    )
    config = load_mcp_config(config_path=cfg)
    assert "ddgs" in config.web_search.fallback


def test_load_mcp_config_configured_fallback_allowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = tmp_path / "mcp.toml"
    cfg.write_text(
        textwrap.dedent("""\
            [web_search]
            fallback = ["searxng"]

            [web_search.backends.searxng]
            backend = "searxng"
            url = "https://search.example.com"
        """),
        encoding="utf-8",
    )
    config = load_mcp_config(config_path=cfg)
    assert "searxng" in config.web_search.fallback


# ---------------------------------------------------------------------------
# Secret scrubbing
# ---------------------------------------------------------------------------


def test_load_mcp_config_api_key_not_in_logs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Resolved api_key values must never appear in loguru output."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = tmp_path / "mcp.toml"
    secret = "super-secret-tavily-key-xyz987"
    cfg.write_text(
        textwrap.dedent(f"""\
            [web_search.backends.tavily]
            backend = "tavily"
            api_key = "{secret}"
        """),
        encoding="utf-8",
    )

    records: list[str] = []
    sink_id = logger.add(lambda msg: records.append(str(msg)), format="{message}")
    try:
        config = load_mcp_config(config_path=cfg)
    finally:
        logger.remove(sink_id)

    assert config.web_search.backends["tavily"].api_key == secret, "secret must be in model"
    joined = "\n".join(records)
    assert secret not in joined, f"api_key leaked into logs: {joined!r}"


# ---------------------------------------------------------------------------
# Media config tests (Task 4)
# ---------------------------------------------------------------------------


def test_load_mcp_config_media_enabled_in_toml(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Parsing [media] with enabled=true yields enabled MediaConfig."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = tmp_path / "mcp.toml"
    cfg.write_text(
        textwrap.dedent("""\
            [media]
            enabled = true
        """),
        encoding="utf-8",
    )
    config = load_mcp_config(config_path=cfg)
    assert config.media.enabled is True
    assert config.media.max_inline_bytes == DEFAULT_MAX_INLINE_BYTES


def test_load_mcp_config_media_with_custom_max_inline_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Parsing [media] with max_inline_bytes round-trips correctly."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = tmp_path / "mcp.toml"
    cfg.write_text(
        textwrap.dedent("""\
            [media]
            enabled = true
            max_inline_bytes = 10485760
        """),
        encoding="utf-8",
    )
    config = load_mcp_config(config_path=cfg)
    assert config.media.enabled is True
    assert config.media.max_inline_bytes == DEFAULT_MAX_INLINE_BYTES * 2


def test_load_mcp_config_media_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When no [media] section is present, media is disabled by default."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = tmp_path / "mcp.toml"
    cfg.write_text(
        textwrap.dedent("""\
            [web_search]
            enabled = true
        """),
        encoding="utf-8",
    )
    config = load_mcp_config(config_path=cfg)
    assert config.media.enabled is False
    assert config.media.max_inline_bytes == DEFAULT_MAX_INLINE_BYTES


def test_load_mcp_config_media_rejects_invalid_max_inline_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """media.max_inline_bytes must be positive."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    cfg = tmp_path / "mcp.toml"
    cfg.write_text(
        textwrap.dedent("""\
            [media]
            enabled = true
            max_inline_bytes = 0
        """),
        encoding="utf-8",
    )
    with pytest.raises(SystemExit) as exc_info:
        load_mcp_config(config_path=cfg)
    assert exc_info.value.code == 1
