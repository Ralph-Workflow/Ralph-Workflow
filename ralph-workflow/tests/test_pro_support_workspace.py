from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ralph.pro_support.workspace import resolve_pro_workspace

if TYPE_CHECKING:
    import pytest


def test_resolve_pro_workspace_uses_env_when_present() -> None:
    env = {"RALPH_WORKSPACE": "/tmp/alpha"}
    result = resolve_pro_workspace(env=env, fallback=Path("/tmp/beta"))
    assert result == Path("/tmp/alpha").resolve()


def test_resolve_pro_workspace_uses_fallback_when_env_empty() -> None:
    assert resolve_pro_workspace(env={}, fallback=Path("/tmp/beta")) == Path("/tmp/beta").resolve()
    assert (
        resolve_pro_workspace(env={"RALPH_WORKSPACE": ""}, fallback=Path("/tmp/beta"))
        == Path("/tmp/beta").resolve()
    )


def test_resolve_pro_workspace_default_fallback_is_cwd() -> None:
    assert resolve_pro_workspace(env={}) == Path.cwd().resolve()


def test_resolve_pro_workspace_expanduser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake_home = (tmp_path / "home").resolve()
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    result = resolve_pro_workspace(env={"RALPH_WORKSPACE": "~"}, fallback=Path("/nope"))
    assert result == fake_home


def test_resolve_pro_workspace_resolves_relative_paths(tmp_path: Path) -> None:
    env = {"RALPH_WORKSPACE": "subdir"}
    result = resolve_pro_workspace(env=env, fallback=tmp_path / "ignored")
    assert result.is_absolute()
    assert result == Path("subdir").resolve()


def test_resolve_pro_workspace_handles_dotdot(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    inside = base / "inside"
    inside.mkdir()
    env = {"RALPH_WORKSPACE": str(base / "inside" / "..")}
    result = resolve_pro_workspace(env=env, fallback=Path("/nope"))
    assert result == base.resolve()
