from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace
from ralph.workspace.probe import DefaultWorkspaceChangeProbe

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


class _ReportedWorkspace(FsWorkspace):
    reported: tuple[str, ...] = ()

    def peek_dirty_paths(self) -> Iterable[object]:
        return self.reported


def _workspace(root: Path) -> _ReportedWorkspace:
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    return _ReportedWorkspace(root)


def test_a_file_reported_dirty_without_a_content_change_is_not_a_change(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("planning", workspace)

    workspace.reported = ("README.md",)

    assert probe.has_changed("planning", workspace) is False


def test_a_file_reported_dirty_after_its_content_changed_is_a_change(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("planning", workspace)

    (tmp_path / "README.md").write_text("hello, edited\n", encoding="utf-8")
    workspace.reported = ("README.md",)

    assert probe.has_changed("planning", workspace) is True


def test_a_new_file_reported_dirty_is_a_change(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    probe = DefaultWorkspaceChangeProbe()
    probe.observe_before("planning", workspace)

    (tmp_path / "notes.md").write_text("new\n", encoding="utf-8")
    workspace.reported = ("notes.md",)

    assert probe.has_changed("planning", workspace) is True
