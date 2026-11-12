from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts.file_backend import PathFileBackend
from ralph.skills import upstream
from ralph.skills._ownership import (
    managed_content_matches,
    read_managed_marker,
    verified_recorded_files,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.skills.upstream import HttpClient, HttpResponse

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

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
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _archive_bytes(tag: str, skills: Mapping[str, Mapping[str, bytes]]) -> bytes:
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
    return sorted(path.name for path in root.iterdir())


def _managed_manifest(skill_dir: Path) -> dict[str, str]:
    marker = read_managed_marker(skill_dir)
    assert marker is not None
    recorded = verified_recorded_files(marker)
    assert recorded is not None
    return recorded


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


def _install_fixture_release(tag: str = "v-test-1") -> Path:
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
    real_write_bytes = PathFileBackend.write_bytes

    def _write_bytes(self: PathFileBackend, path: Path, data: bytes) -> None:
        writes.append(path.name)
        if len(writes) > 2:
            raise ValueError("injected staging failure")
        real_write_bytes(self, path, data)

    monkeypatch.setattr(PathFileBackend, "write_bytes", _write_bytes)
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

    def _write_bytes(self: PathFileBackend, path: Path, data: bytes) -> None:
        raise OSError("injected write failure")

    monkeypatch.setattr(PathFileBackend, "write_bytes", _write_bytes)
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


def test_a_rollback_restores_the_skill_published_before_the_failure(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install_fixture_release()
    published = root / "test-driven-development"
    failing = root / "systematic-debugging"
    untouched = root / "verification-before-completion"
    published_before = _tree_snapshot(published)
    failing_before = _tree_snapshot(failing)
    untouched_before = _tree_snapshot(untouched)
    siblings_before = {
        sibling: _tree_snapshot(sibling) for sibling in upstream.sibling_skills_roots()
    }
    updated = {name: dict(files) for name, files in _SKILL_FILES.items()}
    updated["test-driven-development"] = {
        **_SKILL_FILES["test-driven-development"],
        "new-companion.md": b"# New companion\n",
    }
    client, _calls = _client_for("v-test-2", updated)
    real_replace = os.replace

    def _replace(
        src: str | os.PathLike[str],
        dst: str | os.PathLike[str],
        *args: object,
        **kwargs: object,
    ) -> None:
        source = Path(src)
        destination = Path(dst)
        if source.name.startswith(upstream.STAGING_PREFIX) and destination == failing:
            raise OSError("injected publish failure")
        real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", _replace)
    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert outcome.status == "failed"
    assert _tree_snapshot(published) == published_before
    assert not (published / "new-companion.md").exists()
    assert managed_content_matches(published, _managed_manifest(published))
    assert _tree_snapshot(failing) == failing_before
    assert _tree_snapshot(untouched) == untouched_before
    for sibling, snapshot in siblings_before.items():
        assert _tree_snapshot(sibling) == snapshot


def test_a_failure_during_the_restore_keeps_the_backup_on_disk(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _install_fixture_release()
    published = root / "test-driven-development"
    failing = root / "systematic-debugging"
    backup = published.with_name(f".{published.name}{upstream.BACKUP_SUFFIX}")
    untouched_before = _tree_snapshot(root / "verification-before-completion")
    updated = {name: dict(files) for name, files in _SKILL_FILES.items()}
    updated["test-driven-development"] = {
        **_SKILL_FILES["test-driven-development"],
        "new-companion.md": b"# New companion\n",
    }
    client, _calls = _client_for("v-test-2", updated)
    real_replace = os.replace

    def _replace(
        src: str | os.PathLike[str],
        dst: str | os.PathLike[str],
        *args: object,
        **kwargs: object,
    ) -> None:
        source = Path(src)
        destination = Path(dst)
        if source.name.startswith(upstream.STAGING_PREFIX) and destination == failing:
            raise OSError("injected publish failure")
        if source == backup and destination == published:
            raise OSError("injected restore failure")
        real_replace(src, dst, *args, **kwargs)

    def _copytree(*args: object, **kwargs: object) -> None:
        raise OSError("injected restore failure")

    monkeypatch.setattr(os, "replace", _replace)
    monkeypatch.setattr(shutil, "copytree", _copytree)
    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert outcome.status == "failed"
    assert backup.is_dir()
    assert (backup / "SKILL.md").read_bytes() == _SKILL_FILES["test-driven-development"]["SKILL.md"]
    assert not (backup / "new-companion.md").exists()
    assert _tree_snapshot(root / "verification-before-completion") == untouched_before

    monkeypatch.setattr(os, "replace", real_replace)
    retry_client, _retry_calls = _client_for("v-test-3", _SKILL_FILES)
    retry = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=49), http=retry_client
    )

    assert retry.status == "updated"
    assert published.is_dir()
    assert (published / "SKILL.md").read_bytes() == _SKILL_FILES["test-driven-development"][
        "SKILL.md"
    ]


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


def test_a_nested_reference_resolves_against_the_referring_file(sandbox: Path) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `a.md`.\n",
        "docs/a.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "a.md").read_bytes() == b"# Companion\n"


def test_a_missing_in_tree_parent_relative_reference_is_refused(sandbox: Path) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `../shared/g.md`.\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("shared/g.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_an_in_tree_parent_relative_reference_that_is_carried_is_accepted(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `../shared/g.md`.\n",
        "shared/g.md": b"# Shared\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "shared" / "g.md").read_bytes() == b"# Shared\n"


def test_a_root_relative_reference_that_is_missing_is_refused(sandbox: Path) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nSee `README.md`.\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("README.md" in message for message in outcome.messages)


def test_escaping_and_external_references_are_permitted(sandbox: Path) -> None:
    escaping = {name: dict(files) for name, files in _SKILL_FILES.items()}
    escaping["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\nSee `../../outside.md`, `/etc/x.md`, `~/y.md`, "
            b"`https://example.com/doc.md` and `docs/guide.md#section`.\n"
        ),
    }
    client, _calls = _client_for("v-test-1", escaping)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_an_unrecognized_suffix_reference_is_refused(sandbox: Path) -> None:
    raw = {name: dict(files) for name, files in _SKILL_FILES.items()}
    raw["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `notes.raw`.\n",
    }
    client, _calls = _client_for("v-test-1", raw)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/notes.raw" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_reference_without_a_file_extension_is_permitted(sandbox: Path) -> None:
    bare = {name: dict(files) for name, files in _SKILL_FILES.items()}
    bare["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `notes` for the details.\n",
    }
    client, _calls = _client_for("v-test-1", bare)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_directory_style_reference_is_permitted(sandbox: Path) -> None:
    directory = {name: dict(files) for name, files in _SKILL_FILES.items()}
    directory["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee `assets/` for the details.\n",
    }
    client, _calls = _client_for("v-test-1", directory)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_code_identifier_or_version_token_is_permitted(sandbox: Path) -> None:
    identifiers = {name: dict(files) for name, files in _SKILL_FILES.items()}
    identifiers["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\nThe test read `context.tempDir` and the runtime is `python3.12`.\n"
        ),
    }
    client, _calls = _client_for("v-test-1", identifiers)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_deeply_nested_reference_that_is_carried_is_accepted(sandbox: Path) -> None:
    deep = {name: dict(files) for name, files in _SKILL_FILES.items()}
    deep["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/deep/guide.md": b"# Guide\n\nSee `a.md`.\n",
        "docs/deep/a.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", deep)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "deep" / "a.md").read_bytes() == (
        b"# Companion\n"
    )


def test_a_deeply_nested_reference_that_is_missing_is_refused(sandbox: Path) -> None:
    deep = {name: dict(files) for name, files in _SKILL_FILES.items()}
    deep["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/deep/guide.md": b"# Guide\n\nSee `a.md`.\n",
    }
    client, _calls = _client_for("v-test-1", deep)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/deep/a.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_reference_style_link_that_is_carried_is_accepted(sandbox: Path) -> None:
    styled = {name: dict(files) for name, files in _SKILL_FILES.items()}
    styled["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee the [companion][ref].\n\n[ref]: a.md\n",
        "docs/a.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", styled)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "a.md").read_bytes() == b"# Companion\n"


def test_a_reference_style_link_that_is_missing_is_refused(sandbox: Path) -> None:
    styled = {name: dict(files) for name, files in _SKILL_FILES.items()}
    styled["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee the [companion][ref].\n\n[ref]: missing.md\n",
    }
    client, _calls = _client_for("v-test-1", styled)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert any("docs/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_link_with_a_non_file_url_scheme_is_permitted(sandbox: Path) -> None:
    external = {name: dict(files) for name, files in _SKILL_FILES.items()}
    external["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\n"
            b"[mail](mailto:someone@example.com), "
            b"[plain](http://example.com/doc.md), "
            b"and [ftp](ftp://example.com/doc.md).\n"
        ),
    }
    client, _calls = _client_for("v-test-1", external)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()


def test_a_skill_whose_companions_are_all_carried_installs(sandbox: Path) -> None:
    complete = {name: dict(files) for name, files in _SKILL_FILES.items()}
    complete["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n\nSee `docs/guide.md`.\n",
        "docs/guide.md": b"# Guide\n\nSee `a.md` and `../shared/g.md`.\n",
        "docs/a.md": b"# Companion\n",
        "shared/g.md": b"# Shared\n",
    }
    client, _calls = _client_for("v-test-1", complete)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    installed = root / "systematic-debugging"
    assert (installed / "docs" / "a.md").read_bytes() == b"# Companion\n"
    assert (installed / "shared" / "g.md").read_bytes() == b"# Shared\n"


def test_a_nested_reference_with_fragment_is_accepted_when_present(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](a.md#section).\n",
        "docs/a.md": b"# Companion\n\n## Section\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "a.md").is_file()


def test_a_nested_reference_with_fragment_is_refused_when_missing(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [companion](missing.md#section).\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_nested_reference_with_title_is_accepted_when_present(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nSee [companion](a.md "Companion Title").\n',
        "docs/a.md": b"# Companion\n",
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "a.md").is_file()


def test_a_nested_reference_with_title_is_refused_when_missing(
    sandbox: Path,
) -> None:
    nested = {name: dict(files) for name, files in _SKILL_FILES.items()}
    nested["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nSee [companion](missing.md "Companion Title").\n',
    }
    client, _calls = _client_for("v-test-1", nested)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("docs/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_parent_relative_reference_with_fragment_is_accepted_when_present(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [shared](../shared/g.md#section).\n",
        "shared/g.md": b"# Shared\n\n## Section\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "shared" / "g.md").is_file()


def test_a_parent_relative_reference_with_fragment_is_refused_when_missing(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b"# Guide\n\nSee [shared](../shared/missing.md#section).\n",
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("shared/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_a_parent_relative_reference_with_title_is_accepted_when_present(
    sandbox: Path,
) -> None:
    carried = {name: dict(files) for name, files in _SKILL_FILES.items()}
    carried["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nSee [shared](../shared/g.md "Shared Guide").\n',
        "shared/g.md": b"# Shared\n",
    }
    client, _calls = _client_for("v-test-1", carried)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "shared" / "g.md").is_file()


def test_a_parent_relative_reference_with_title_is_refused_when_missing(
    sandbox: Path,
) -> None:
    missing = {name: dict(files) for name, files in _SKILL_FILES.items()}
    missing["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": b'# Guide\n\nSee [shared](../shared/missing.md "Shared Guide").\n',
    }
    client, _calls = _client_for("v-test-1", missing)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert any("shared/missing.md" in message for message in outcome.messages)
    root = upstream.canonical_skills_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()


def test_pure_fragment_and_external_link_destinations_are_permitted(
    sandbox: Path,
) -> None:
    escaping = {name: dict(files) for name, files in _SKILL_FILES.items()}
    escaping["systematic-debugging"] = {
        "SKILL.md": b"# Systematic debugging\n",
        "docs/guide.md": (
            b"# Guide\n\n"
            b"See [section](#section), "
            b"[empty fragment](#), "
            b'[external](https://example.com/doc.md#sec "Ext"), '
            b'[escaping](../../outside.md#sec "Esc"), '
            b"and [absolute](/etc/x.md#sec 'Abs').\n"
        ),
    }
    client, _calls = _client_for("v-test-1", escaping)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = upstream.canonical_skills_root()
    assert (root / "systematic-debugging" / "docs" / "guide.md").is_file()
