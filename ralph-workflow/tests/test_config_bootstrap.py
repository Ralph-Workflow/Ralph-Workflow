"""Black-box tests for ralph.config.bootstrap and related first-run behavior."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

import ralph.config.loader as loader_module
import ralph.policy
from ralph.config.bootstrap import (
    ensure_global_config,
    ensure_global_mcp_config,
    ensure_local_configs,
    regenerate_all,
    resolve_global_config_dir,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest

_EXPECTED_LOCAL_CONFIG_COUNT = 2
_EXPECTED_REGENERATE_COUNT = 4
_DEFAULT_DEVELOPER_ITERS = 5


def test_ensure_global_config_creates_when_absent(tmp_path: Path) -> None:
    result = ensure_global_config(tmp_path)
    target = tmp_path / "ralph-workflow.toml"

    assert target.exists()
    assert result.action == "created"
    assert result.backup is None
    assert isinstance(tomllib.loads(target.read_text()), dict)


def test_ensure_global_config_idempotent(tmp_path: Path) -> None:
    ensure_global_config(tmp_path)
    target = tmp_path / "ralph-workflow.toml"
    mtime_after_first = target.stat().st_mtime

    result2 = ensure_global_config(tmp_path)
    assert result2.action == "skipped"
    assert target.stat().st_mtime == mtime_after_first


def test_ensure_global_config_force_creates_backup(tmp_path: Path) -> None:
    target = tmp_path / "ralph-workflow.toml"
    target.write_text("# MINE", encoding="utf-8")

    result = ensure_global_config(tmp_path, force=True)

    backup = target.with_suffix(".toml.bak")
    assert backup.exists()
    assert backup.read_text(encoding="utf-8") == "# MINE"
    assert target.read_text(encoding="utf-8").startswith("#")
    assert result.action == "regenerated"
    assert result.backup == backup


def test_ensure_global_mcp_config_creates(tmp_path: Path) -> None:
    result = ensure_global_mcp_config(tmp_path)
    target = tmp_path / "ralph-workflow-mcp.toml"

    assert target.exists()
    assert result.action == "created"
    assert result.backup is None
    assert isinstance(tomllib.loads(target.read_text()), dict)


def test_ensure_local_configs_creates_both(tmp_path: Path) -> None:
    agent_dir = tmp_path / ".agent"
    results = ensure_local_configs(agent_dir)

    assert (agent_dir / "ralph-workflow.toml").exists()
    assert (agent_dir / "mcp.toml").exists()
    assert isinstance(tomllib.loads((agent_dir / "ralph-workflow.toml").read_text()), dict)
    assert isinstance(tomllib.loads((agent_dir / "mcp.toml").read_text()), dict)
    assert len(results) == _EXPECTED_LOCAL_CONFIG_COUNT
    assert all(r.action == "created" for r in results)


def test_regenerate_all_force_creates_backups(tmp_path: Path) -> None:
    global_dir = tmp_path / "g"
    agent_dir = tmp_path / "a"
    global_dir.mkdir()
    agent_dir.mkdir()

    sentinel = "# SENTINEL"
    (global_dir / "ralph-workflow.toml").write_text(sentinel, encoding="utf-8")
    (global_dir / "ralph-workflow-mcp.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "ralph-workflow.toml").write_text(sentinel, encoding="utf-8")
    (agent_dir / "mcp.toml").write_text(sentinel, encoding="utf-8")

    results = regenerate_all(global_dir=global_dir, agent_dir=agent_dir)

    assert len(results) == _EXPECTED_REGENERATE_COUNT
    assert all(r.action == "regenerated" for r in results)

    for result in results:
        assert result.backup is not None
        assert result.backup.exists()
        assert result.backup.read_text(encoding="utf-8") == sentinel
        assert isinstance(tomllib.loads(result.path.read_text()), dict)


def test_resolve_global_config_dir_honors_xdg(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    assert resolve_global_config_dir() == tmp_path


def test_resolve_global_config_dir_defaults_to_home(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert resolve_global_config_dir() == Path.home() / ".config"


def test_ensure_global_config_round_trips_through_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_global_config(tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    scope = WorkspaceScope(tmp_path)
    cfg = loader_module.load_config(workspace_scope=scope)
    assert cfg.general.developer_iters == _DEFAULT_DEVELOPER_ITERS


def test_bundled_global_template_parses_as_valid_toml() -> None:
    template = Path(ralph.policy.__file__).parent / "defaults" / "ralph-workflow.toml"
    content = template.read_text(encoding="utf-8")
    result = tomllib.loads(content)
    assert isinstance(result, dict)
