"""Tests for the fs-health diagnostic."""

from __future__ import annotations

from pathlib import Path

from ralph.diagnostics import fs_health as fs_health_module
from ralph.diagnostics.fs_health import (
    _JOURNAL_WARN_BYTES,
    FsHealth,
    _probe_journal_size,
    _probe_spotlight,
    _volume_root,
)


def test_volume_root_for_external_volume() -> None:
    assert _volume_root(Path("/Volumes/Disk X/proj/ws")) == Path("/Volumes/Disk X")


def test_volume_root_for_boot_volume() -> None:
    assert _volume_root(Path("/Users/me/proj")) == Path("/")


def test_probe_journal_size_walks_journal_dir(tmp_path: Path) -> None:
    """The journal probe sums file sizes under ``<journal>/.fseventsd``."""
    journal = tmp_path / ".fseventsd"
    journal.mkdir()
    (journal / "event-1").write_bytes(b"x" * 1024)
    (journal / "event-2").write_bytes(b"y" * 2048)
    assert _probe_journal_size(journal) == 3072


def test_probe_journal_size_returns_none_for_missing_dir(tmp_path: Path) -> None:
    assert _probe_journal_size(tmp_path / ".fseventsd") is None


def test_probe_journal_size_thresholds_warning(tmp_path: Path) -> None:
    """A journal exceeding _JOURNAL_WARN_BYTES triggers the warning."""

    def fake_run(cmd: object, **kwargs: object) -> object:
        class _R:
            returncode = 0
            stdout = "/Volumes/X:\n\tIndexing enabled. \n"

        return _R()

    journal = tmp_path / ".fseventsd"
    journal.mkdir()
    (journal / "big").write_bytes(b"x" * (_JOURNAL_WARN_BYTES + 1))

    health = FsHealth(
        volume_root=str(tmp_path),
        spotlight_indexing_enabled=_probe_spotlight(tmp_path, fake_run),
        fsevents_journal_bytes=_probe_journal_size(journal),
    )
    if health.spotlight_indexing_enabled is True:
        health.warnings.append(
            f"Spotlight indexing is enabled on {tmp_path}. "
            "Disable with `sudo mdutil -i off <volume>`."
        )
    if (
        health.fsevents_journal_bytes is not None
        and health.fsevents_journal_bytes > _JOURNAL_WARN_BYTES
    ):
        human_mb = health.fsevents_journal_bytes / (1024 * 1024)
        health.warnings.append(
            f"fseventsd journal on {tmp_path} is {human_mb:.1f} MB "
            "(threshold 50 MB)."
        )
    assert len(health.warnings) == 2


def test_warns_on_spotlight_and_fat_journal(
    tmp_path: Path, monkeypatch: object
) -> None:
    """End-to-end: FsHealth.gather() emits both warnings when both probes trigger.

    Stubs ``_volume_root`` so the journal probe targets the tmp_path
    directory (the production resolver treats ``tmp_path`` as a
    boot-volume path and would otherwise look at ``/.fseventsd``,
    which is not writable in test environments). The fake
    ``run_command`` makes Spotlight report enabled; the inflated
    ``.fseventsd`` file trips the journal-size threshold. Both
    warnings must land in the ``FsHealth.warnings`` list so the
    operator sees both mitigations at once.
    """
    monkeypatch.setattr(fs_health_module, "_volume_root", lambda _p: tmp_path)

    def fake_run(cmd: object, **kwargs: object) -> object:
        class _R:
            returncode = 0
            stdout = f"{tmp_path}:\n\tIndexing enabled. \n"

        return _R()

    journal = tmp_path / ".fseventsd"
    journal.mkdir()
    (journal / "big").write_bytes(b"x" * (_JOURNAL_WARN_BYTES + 1))

    health = FsHealth.gather(tmp_path, run_command=fake_run)

    assert health.spotlight_indexing_enabled is True
    assert health.fsevents_journal_bytes is not None
    assert health.fsevents_journal_bytes > _JOURNAL_WARN_BYTES
    assert len(health.warnings) == 2
    assert any("Spotlight indexing is enabled" in w for w in health.warnings)
    assert any("fseventsd journal" in w for w in health.warnings)
    assert any("50" in w for w in health.warnings)
