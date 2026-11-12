"""Transactional-install tests for the universal upstream skill updater.

The install path is exercised through the injected HTTP seam: no test opens
a socket and no test touches a path outside its sandbox. The suite pins the
three install guarantees of the updater: every payload is validated BEFORE
the first write, each skill directory is published atomically, and a failure
leaves the previously installed skills byte-identical. It also pins the
ownership gate of the sibling mirror: an entry Ralph cannot prove it owns is
preserved and reported, never removed.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.skills import upstream

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.skills.upstream import HttpClient, HttpResponse

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

#: The companion files every universal skill of the fixture release carries.
_SKILL_FILES: dict[str, dict[str, bytes]] = {
    "test-driven-development": {
        "SKILL.md": b"# Test driven development\n\nWrite the failing test first.\n",
        "test-first.md": b"# Test first\n",
    },
    "systematic-debugging": {
        "SKILL.md": b"# Systematic debugging\n",
        "condition-based-waiting.md": (
            b"# Condition based waiting\n\nSee `condition-based-waiting-example.ts`.\n"
        ),
        "condition-based-waiting-example.ts": b"export const wait = (): boolean => true;\n",
    },
    "verification-before-completion": {
        "SKILL.md": b"# Verification before completion\n",
        "evidence-checklist.md": b"# Evidence checklist\n",
    },
}


def _release_body(tag: str) -> bytes:
    """Return the release payload the upstream API answers with."""
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _archive_bytes(tag: str, skills: Mapping[str, Mapping[str, bytes]]) -> bytes:
    """Return the archive bytes of a release carrying ``skills``."""
    root = f"superpowers-{tag.lstrip('v')}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for skill, files in skills.items():
            for relative, data in files.items():
                info = tarfile.TarInfo(f"{root}/skills/{skill}/{relative}")
                info.size = len(data)
                info.mode = 0o755 if relative.endswith(".sh") else 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _client_for(
    tag: str, skills: Mapping[str, Mapping[str, bytes]]
) -> tuple[HttpClient, list[str]]:
    """Return a fake client serving one release, plus its request log."""
    calls: list[str] = []
    responses = {
        upstream.RELEASES_API_URL: (200, _release_body(tag)),
        upstream.ARCHIVE_URL_TEMPLATE.format(tag=tag): (
            200,
            _archive_bytes(tag, skills),
        ),
    }

    def _get(url: str, timeout: float) -> HttpResponse:
        calls.append(url)
        return responses.get(url, (404, b""))

    return (_get, calls)


def _tree_snapshot(root: Path) -> dict[str, str]:
    """Return a content snapshot of every file below ``root``."""
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = "symlink:" + path.readlink().as_posix()
            continue
        if path.is_file():
            snapshot[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


def _root_entries(root: Path) -> list[str]:
    """Return the names of the entries the skills root carries."""
    return sorted(path.name for path in root.iterdir())


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user-global roots, the cache and the state file at a sandbox."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


def _install_fixture_release(tag: str = "v-test-1") -> Path:
    """Install the fixture release through the injected seam and return the root."""
    client, _calls = _client_for(tag, _SKILL_FILES)
    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    assert outcome.status == "updated"
    return upstream.canonical_skills_root()


def test_a_publication_a_killed_process_left_half_finished_is_restored(
    sandbox: Path,
) -> None:
    root = _install_fixture_release()
    live = root / "test-driven-development"
    backup = live.with_name(f".{live.name}.ralph-backup")
    live.replace(backup)
    assert not live.exists()
    client, _calls = _client_for("v-test-2", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert outcome.status == "updated"
    assert not backup.exists()
    assert _root_entries(root) == sorted(upstream.UNIVERSAL_SKILL_NAMES)
    assert (live / "SKILL.md").read_bytes() == _SKILL_FILES["test-driven-development"]["SKILL.md"]


def test_an_orphan_staging_directory_is_reclaimed_by_the_next_update(
    sandbox: Path,
) -> None:
    root = _install_fixture_release()
    orphan = root / f"{upstream.STAGING_PREFIX}systematic-debugging-deadbeef"
    orphan.mkdir()
    (orphan / "SKILL.md").write_text("# half written\n", encoding="utf-8")
    client, _calls = _client_for("v-test-2", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert outcome.status == "updated"
    assert not orphan.exists()
    assert _root_entries(root) == sorted(upstream.UNIVERSAL_SKILL_NAMES)


def test_a_failure_that_is_not_an_io_failure_rolls_the_staging_back(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install_fixture_release()
    before = _tree_snapshot(root)
    client, _calls = _client_for("v-test-2", _SKILL_FILES)
    writes: list[str] = []
    real_write_bytes = Path.write_bytes

    def _write_bytes(self: Path, data: bytes) -> int:
        writes.append(self.name)
        if len(writes) > 2:
            raise ValueError("injected staging failure")
        return real_write_bytes(self, data)

    monkeypatch.setattr(Path, "write_bytes", _write_bytes)
    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert outcome.status == "failed"
    assert _tree_snapshot(root) == before
    assert _root_entries(root) == sorted(upstream.UNIVERSAL_SKILL_NAMES)


def test_a_publication_left_half_finished_is_repaired_before_the_attempt_window(
    sandbox: Path,
) -> None:
    root = _install_fixture_release()
    live = root / "test-driven-development"
    backup = live.with_name(f".{live.name}{upstream.BACKUP_SUFFIX}")
    live.replace(backup)
    assert not live.exists()
    client, _calls = _client_for("v-test-2", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=1), http=client
    )

    assert outcome.status == "skipped"
    assert live.is_dir()
    assert not backup.exists()
    assert _root_entries(root) == sorted(upstream.UNIVERSAL_SKILL_NAMES)


def test_a_valid_release_installs_every_skill_and_mirrors_the_siblings(
    sandbox: Path,
) -> None:
    client, calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert outcome.installed == upstream.UNIVERSAL_SKILL_NAMES
    assert len(calls) == 2
    root = upstream.canonical_skills_root()
    for skill, files in _SKILL_FILES.items():
        for relative, data in files.items():
            assert (root / skill / relative).read_bytes() == data
    for sibling in upstream.sibling_skills_roots():
        for skill in upstream.UNIVERSAL_SKILL_NAMES:
            assert (sibling / skill / "SKILL.md").is_file()
            assert (sibling / skill / upstream.MANAGED_MARKER_NAME).is_file()


def test_a_payload_without_a_skill_file_is_refused_and_keeps_the_install(
    sandbox: Path,
) -> None:
    root = _install_fixture_release()
    refused = root / "systematic-debugging"
    before = _tree_snapshot(refused)
    incomplete = {name: dict(files) for name, files in _SKILL_FILES.items()}
    incomplete["systematic-debugging"].pop("SKILL.md")
    client, _calls = _client_for("v-test-2", incomplete)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("SKILL.md" in message for message in outcome.messages)
    assert _tree_snapshot(refused) == before


def test_a_reference_that_matches_no_installed_relative_path_is_refused(
    sandbox: Path,
) -> None:
    mismatched = {name: dict(files) for name, files in _SKILL_FILES.items()}
    mismatched["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nSee `docs/example.ts`.\n",
        "example.ts": b"export const example = 1;\n",
    }
    client, _calls = _client_for("v-test-1", mismatched)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/example.ts" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_reference_that_matches_the_exact_relative_path_is_accepted(
    sandbox: Path,
) -> None:
    exact = {name: dict(files) for name, files in _SKILL_FILES.items()}
    exact["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nSee `docs/example.ts`.\n",
        "docs/example.ts": b"export const example = 1;\n",
    }
    client, _calls = _client_for("v-test-1", exact)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "example.ts").read_bytes() == (
        b"export const example = 1;\n"
    )


def test_an_injected_write_failure_keeps_the_install_byte_identical(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install_fixture_release()
    before = _tree_snapshot(root)
    client, _calls = _client_for("v-test-2", _SKILL_FILES)

    def _write_bytes(self: Path, data: bytes) -> int:
        raise OSError("injected write failure")

    monkeypatch.setattr(Path, "write_bytes", _write_bytes)
    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert _tree_snapshot(root) == before
    assert _root_entries(root) == sorted(upstream.UNIVERSAL_SKILL_NAMES)


def test_an_interrupted_publish_keeps_the_install_byte_identical(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install_fixture_release()
    before = _tree_snapshot(root)
    client, _calls = _client_for("v-test-2", _SKILL_FILES)
    live = root / "test-driven-development"
    real_replace = os.replace

    def _replace(
        src: str | os.PathLike[str],
        dst: str | os.PathLike[str],
        *args: object,
        **kwargs: object,
    ) -> None:
        if Path(dst) == live:
            raise OSError("injected publish failure")
        real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", _replace)
    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert _tree_snapshot(root) == before
    assert _root_entries(root) == sorted(upstream.UNIVERSAL_SKILL_NAMES)


def test_a_successful_install_leaves_no_staging_directory_behind(sandbox: Path) -> None:
    _install_fixture_release()
    root = upstream.canonical_skills_root()

    assert _root_entries(root) == sorted(upstream.UNIVERSAL_SKILL_NAMES)


def test_a_sibling_symlink_pointing_elsewhere_is_preserved_and_reported(
    sandbox: Path,
) -> None:
    sibling_root = upstream.sibling_skills_roots()[0]
    elsewhere = sibling_root / "elsewhere"
    elsewhere.mkdir(parents=True)
    (elsewhere / "notes.md").write_text("# my own notes\n", encoding="utf-8")
    sibling_dir = sibling_root / "test-driven-development"
    sibling_dir.symlink_to(elsewhere, target_is_directory=True)
    client, _calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert sibling_dir.is_symlink()
    assert sibling_dir.resolve() == elsewhere.resolve()
    assert any(
        "test-driven-development" in message and "preserved" in message
        for message in outcome.messages
    )


def test_a_sibling_symlink_that_points_at_the_canonical_skill_is_left_alone(
    sandbox: Path,
) -> None:
    root = _install_fixture_release()
    sibling_root = upstream.sibling_skills_roots()[0]
    sibling_dir = sibling_root / "test-driven-development"
    canonical = root / "test-driven-development"
    assert sibling_dir.is_symlink()
    assert sibling_dir.resolve() == canonical.resolve()
    before = _tree_snapshot(root)
    client, _calls = _client_for("v-test-2", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert outcome.status == "updated"
    assert sibling_dir.is_symlink()
    assert sibling_dir.resolve() == canonical.resolve()
    assert _tree_snapshot(root) != before
