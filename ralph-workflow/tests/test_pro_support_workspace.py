"""Black-box unit tests for :mod:`ralph.pro_support.workspace`."""

from __future__ import annotations

from pathlib import Path

from ralph.pro_support.workspace import resolve_pro_workspace


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


def test_resolve_pro_workspace_expanduser() -> None:
    home = Path.home().resolve()
    result = resolve_pro_workspace(env={"RALPH_WORKSPACE": "~"}, fallback=Path("/nope"))
    assert result == home


def test_resolve_pro_workspace_resolves_relative_paths(tmp_path: Path) -> None:
    env = {"RALPH_WORKSPACE": "subdir"}
    # Use a fallback that lets us construct a known absolute result
    result = resolve_pro_workspace(env=env, fallback=tmp_path / "ignored")
    # relative RALPH_WORKSPACE is resolved via Path.resolve, which makes
    # it absolute against the current process cwd; this test just
    # ensures the resolver returns an absolute, resolved path
    assert result.is_absolute()
    assert result == Path("subdir").resolve()


def test_resolve_pro_workspace_handles_dotdot(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    inside = base / "inside"
    inside.mkdir()
    # ``..`` should resolve cleanly
    env = {"RALPH_WORKSPACE": str(base / "inside" / "..")}
    result = resolve_pro_workspace(env=env, fallback=Path("/nope"))
    assert result == base.resolve()
