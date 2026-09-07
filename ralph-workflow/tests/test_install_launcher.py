from __future__ import annotations

import os
from pathlib import Path

import pytest

from ralph import install as install_module


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_write_dev_launcher_creates_and_replaces_executable_script(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "bin" / "rdev"
    content = '#!/usr/bin/env bash\nexec uv run --project /tmp/ralph ralph "$@"\n'

    install_module.write_dev_launcher(target, content)

    assert target.read_text(encoding="utf-8") == content
    assert os.access(target, os.X_OK)

    refreshed = '#!/usr/bin/env bash\nexec uv run --project /tmp/ralph-next ralph "$@"\n'
    install_module.write_dev_launcher(target, refreshed)

    assert target.read_text(encoding="utf-8") == refreshed
    assert os.access(target, os.X_OK)


def test_write_dev_launcher_does_not_follow_predictable_staging_symlink(tmp_path: Path) -> None:
    target = tmp_path / "bin" / "rdev"
    target.parent.mkdir()
    victim = tmp_path / "victim"
    victim.write_text("untouched", encoding="utf-8")
    (target.parent / "rdev.staging").symlink_to(victim)

    install_module.write_dev_launcher(target, "#!/bin/sh\nexit 0\n")

    assert victim.read_text(encoding="utf-8") == "untouched"
    assert target.read_text(encoding="utf-8") == "#!/bin/sh\nexit 0\n"


def test_write_dev_launcher_regression_restores_previous_launcher_after_replace_fsync_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "bin" / "rdev"
    target.parent.mkdir()
    target.write_bytes(b"old launcher")
    target.chmod(0o741)
    sync_attempts = 0

    def fail_directory_sync(_descriptor: int) -> None:
        nonlocal sync_attempts
        sync_attempts += 1
        if sync_attempts == 2:
            raise OSError("injected directory fsync failure")

    monkeypatch.setattr(os, "fsync", fail_directory_sync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        install_module.write_dev_launcher(target, "new launcher")

    assert target.read_bytes() == b"old launcher"
    assert target.stat().st_mode & 0o777 == 0o741
    assert sync_attempts == 3


def test_write_dev_launcher_regression_removes_first_launcher_after_replace_fsync_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    target = tmp_path / "bin" / "rdev"
    sync_attempts = 0

    def fail_directory_sync(_descriptor: int) -> None:
        nonlocal sync_attempts
        sync_attempts += 1
        if sync_attempts == 2:
            raise OSError("injected directory fsync failure")

    monkeypatch.setattr(os, "fsync", fail_directory_sync)

    with pytest.raises(OSError, match="injected directory fsync failure"):
        install_module.write_dev_launcher(target, "new launcher")

    assert not target.exists()
    assert sync_attempts == 3
