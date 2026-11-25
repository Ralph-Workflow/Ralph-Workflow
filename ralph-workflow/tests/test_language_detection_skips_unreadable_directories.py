from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest

from ralph.language_detector import get_project_stack
from ralph.language_detector.scanner import collect_signature_files, count_extensions, detect_tests
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def worktree_with_unreadable_rootfs(tmp_path: Path) -> Iterator[Path]:
    (tmp_path / "Cargo.toml").write_text('[package]\nname = "demo"\n', encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "smoke_test.rs").write_text("#[test]\nfn ok() {}\n", encoding="utf-8")
    locked = tmp_path / "tmp" / "guest-image-work" / "probe" / "rootfs" / "root"
    locked.mkdir(parents=True)
    (locked / "secret.py").write_text("x = 1\n", encoding="utf-8")
    locked.chmod(0)
    try:
        if os.access(locked, os.R_OK):
            pytest.skip("running with privileges that ignore directory permissions")
        yield tmp_path
    finally:
        locked.chmod(0o700)


def test_project_stack_is_detected_past_an_unreadable_directory(
    worktree_with_unreadable_rootfs: Path,
) -> None:
    stack = get_project_stack(worktree_with_unreadable_rootfs)
    assert stack.primary_language == "Rust"


def test_an_unreadable_manifest_does_not_stop_stack_detection(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    manifest = tmp_path / "Cargo.toml"
    manifest.write_text('[package]\nname = "demo"\n', encoding="utf-8")
    manifest.chmod(0)
    try:
        if os.access(manifest, os.R_OK):
            pytest.skip("running with privileges that ignore file permissions")
        assert get_project_stack(tmp_path).primary_language == "Rust"
    finally:
        manifest.chmod(0o600)


def test_scanners_skip_an_unreadable_directory_and_keep_scanning(
    worktree_with_unreadable_rootfs: Path,
) -> None:
    workspace = FsWorkspace(worktree_with_unreadable_rootfs)
    assert count_extensions(workspace).get("rs") == 2
    assert collect_signature_files(workspace)["cargo.toml"] == ["Cargo.toml"]
    assert detect_tests(workspace, primary_language="Rust") is True
