from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.server.session_workspace_root import resolve_session_workspace_root

if TYPE_CHECKING:
    from pathlib import Path


def _session_file(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "session.json"
    path.write_text("{}", encoding="utf-8")
    return path


def test_a_session_inside_a_workspace_resolves_to_that_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "project"
    session = _session_file(workspace / ".agent" / "mcp")

    assert resolve_session_workspace_root(session, protected_roots=()) == workspace.resolve()


def test_a_session_directly_under_the_agent_dir_resolves_to_its_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "project"
    session = _session_file(workspace / ".agent")

    assert resolve_session_workspace_root(session, protected_roots=()) == workspace.resolve()


def test_a_stray_agent_dir_above_the_temp_folder_is_never_used(tmp_path: Path) -> None:
    user_temp_parent = tmp_path / "var-folders-user"
    system_temp = user_temp_parent / "T"
    (user_temp_parent / ".agent").mkdir(parents=True)
    session = _session_file(system_temp / "ralph-run-1")

    root = resolve_session_workspace_root(session, protected_roots=(system_temp, user_temp_parent))

    assert root == session.parent.resolve()


def test_a_session_with_no_workspace_never_climbs_to_home(tmp_path: Path) -> None:
    home = tmp_path / "home"
    session = _session_file(home / "scratch" / "one")

    root = resolve_session_workspace_root(session, protected_roots=(home,))

    assert root == session.parent.resolve()
    assert root != home.resolve()
