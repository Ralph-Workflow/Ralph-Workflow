from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ralph.diagnostics import fs_health as fs_health_module
from ralph.diagnostics.fs_health import (
    _JOURNAL_WARN_BYTES,
    FsHealth,
    _probe_journal_size,
    _volume_root,
)


def test_volume_root_for_external_volume() -> None:
    assert _volume_root(Path("/Volumes/Disk X/proj/ws")) == Path("/Volumes/Disk X")


def test_volume_root_for_boot_volume() -> None:
    assert _volume_root(Path("/Users/me/proj")) == Path("/")


def test_probe_journal_size_walks_journal_dir(tmp_path: Path) -> None:
    journal = tmp_path / ".fseventsd"
    journal.mkdir()
    (journal / "event-1").write_bytes(b"x" * 1024)
    (journal / "event-2").write_bytes(b"y" * 2048)
    assert _probe_journal_size(journal) == 3072


def test_probe_journal_size_returns_none_for_missing_dir(tmp_path: Path) -> None:
    assert _probe_journal_size(tmp_path / ".fseventsd") is None


def test_warns_on_spotlight_and_fat_journal(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(fs_health_module, "_volume_root", lambda _p: tmp_path)
    monkeypatch.setattr(
        fs_health_module, "_probe_journal_size", lambda _journal: _JOURNAL_WARN_BYTES + 1
    )
    monkeypatch.setattr(fs_health_module.sys, "platform", "darwin")

    def fake_run(cmd: object, **kwargs: object) -> object:
        class _R:
            returncode = 0
            stdout = f"{tmp_path}:\n\tIndexing enabled. \n"

        return _R()

    health = FsHealth.gather(tmp_path, run_command=fake_run)

    assert health.spotlight_indexing_enabled is True
    assert health.fsevents_journal_bytes is not None
    assert health.fsevents_journal_bytes == _JOURNAL_WARN_BYTES + 1
    assert len(health.warnings) == 2
    assert any("Spotlight indexing is enabled" in w for w in health.warnings)
    assert any("fseventsd journal" in w for w in health.warnings)
    assert any("Ralph will continue with bounded workspace awareness" in w for w in health.warnings)


def test_gather_on_non_darwin_only_sets_volume_root(tmp_path: Path) -> None:

    def fake_run(cmd: object, **kwargs: object) -> object:
        class _R:
            returncode = 0
            stdout = f"{tmp_path}:\n\tIndexing enabled. \n"

        return _R()

    with patch.object(fs_health_module.sys, "platform", "linux"):
        journal = tmp_path / ".fseventsd"
        journal.mkdir()
        (journal / "big").write_bytes(b"x" * 64)
        with patch.object(
            fs_health_module,
            "_probe_journal_size",
            wraps=fs_health_module._probe_journal_size,
        ) as probe_spy:
            health = FsHealth.gather(tmp_path, run_command=fake_run)
            assert probe_spy.call_count == 0, (
                "non-darwin gate must skip the journal probe; "
                f"_probe_journal_size was called {probe_spy.call_count} times"
            )

    assert health.volume_root != ""
    assert health.spotlight_indexing_enabled is None, (
        f"non-darwin host must leave spotlight at None, got {health.spotlight_indexing_enabled!r}"
    )
    assert health.fsevents_journal_bytes is None, (
        f"non-darwin host must leave journal bytes at None, got {health.fsevents_journal_bytes!r}"
    )
    assert health.warnings == [], f"non-darwin host must emit no warnings, got {health.warnings!r}"
