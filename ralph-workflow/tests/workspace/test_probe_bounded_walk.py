from __future__ import annotations

import os
import stat
from itertools import count
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.workspace import fs as fs_module
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.probe import DefaultWorkspaceChangeProbe

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest


def test_probe_stops_walking_once_the_snapshot_limit_is_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    yielded = count()

    def endless_walk(
        top: str, *args: object, **kwargs: object
    ) -> Iterator[tuple[str, list[str], list[str]]]:
        for i in yielded:
            if i > 50_000:
                raise AssertionError("probe walked far past its snapshot limit")
            yield top, [], [f"f{i}.txt"]

    monkeypatch.setattr(fs_module.os, "walk", endless_walk)
    probe = DefaultWorkspaceChangeProbe(max_files=100)
    workspace = FsWorkspace(tmp_path)

    probe.observe_before("development", workspace)
    probe.has_changed("development", workspace)

    assert next(yielded) < 1_000


def test_iter_files_does_not_descend_into_other_filesystems(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "kept.py").write_text("x")
    mounted = tmp_path / "tmp" / "rootfs" / "proc"
    mounted.mkdir(parents=True)
    (mounted / "1").mkdir()
    (mounted / "1" / "status").write_text("x")
    (tmp_path / "tmp" / "rootfs" / "etc.conf").write_text("x")

    real_stat = Path.stat

    def stat_with_mount(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        result = real_stat(self, follow_symlinks=follow_symlinks)
        if not self.is_relative_to(mounted):
            return result
        fields = list(result)
        fields[stat.ST_DEV] = result.st_dev + 1
        return os.stat_result(fields)

    monkeypatch.setattr(Path, "stat", stat_with_mount)
    files = set(FsWorkspace(tmp_path).iter_files(""))

    assert "src/kept.py" in files
    assert "tmp/rootfs/etc.conf" in files
    assert not any(f.startswith("tmp/rootfs/proc") for f in files)


def test_walk_files_never_descends_into_a_skipped_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "kept.md").write_text("x")
    (tmp_path / "wt-02-sibling" / "deep").mkdir(parents=True)
    (tmp_path / "wt-02-sibling" / "deep" / "skipped.md").write_text("x")
    visited: list[str] = []
    real_walk = os.walk

    def recording_walk(top: str) -> Iterator[tuple[str, list[str], list[str]]]:
        for entry in real_walk(top):
            visited.append(entry[0])
            yield entry

    monkeypatch.setattr(fs_module.os, "walk", recording_walk)

    files = list(FsWorkspace(tmp_path).walk_files("", skip_dir=lambda name: name.startswith("wt-")))

    assert files == ["src/kept.md"]
    assert not [root for root in visited if "wt-02-sibling" in root]
