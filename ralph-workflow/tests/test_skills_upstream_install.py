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

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.skills.upstream import HttpClient, HttpResponse

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "RALPH_TEST_CHANGE_REASONS upstream install size split": (
        "assertions moved into tests/test_skills_upstream_install_payloads.py during the repo-structure size split; no assertion was dropped"
    ),
    "test_a_payload_without_a_skill_file_is_refused_and_keeps_the_install": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_reference_that_matches_no_installed_relative_path_is_refused": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_reference_that_matches_the_exact_relative_path_is_accepted": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_nested_reference_resolves_against_the_referring_file": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_missing_in_tree_parent_relative_reference_is_refused": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_an_in_tree_parent_relative_reference_that_is_carried_is_accepted": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_root_relative_reference_that_is_missing_is_refused": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_refused_companion_reference_keeps_the_install": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_an_escaping_reference_is_refused": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_external_references_are_permitted": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_link_reference_to_a_missing_companion_file_is_refused": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_link_reference_to_a_carried_directory_is_accepted": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_directed_reference_to_a_carried_directory_is_accepted": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_an_uncued_worked_example_reference_is_permitted": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_the_upstream_release_reference_shapes_are_permitted": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_an_unrecognized_suffix_reference_is_refused": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_a_directed_code_span_with_a_literal_space_that_is_carried_is_accepted": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_version_companion_is_admitted_and_other_extensionless_members_rejected": (
        "the payload-admission and reference-completeness checks moved into tests/test_skills_upstream_install_payloads.py so this module stays under the repository's 1000-line file limit; every assertion is unchanged and the moved module asserts the same behavior"
    ),
    "test_preexisting_copy_in_alternate_agent_root_suppresses_install": (
        "the alternate-agent-root checks now run through the public update seam ralph.skills.upstream.maybe_update_universal_skills instead of the private presence-detection helpers, so this module asserts that a pre-existing copy in an alternate agent root suppresses the install"
    ),
}

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
    "antislop": {"SKILL.md": b"# Antislop\n\nFilter AI slop.\n", "VERSION": b"3.2.30\n"},
}


def _release_body(tag: str) -> bytes:
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _archive_bytes(
    tag: str, skills: Mapping[str, Mapping[str, bytes]], *, root: str | None = None
) -> bytes:
    top = root if root is not None else f"superpowers-{tag.lstrip('v')}"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for skill, files in skills.items():
            for relative, data in files.items():
                info = tarfile.TarInfo(f"{top}/skills/{skill}/{relative}")
                info.size = len(data)
                info.mode = 0o755 if relative.endswith(".sh") else 0o644
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _source_skills(
    skills: Mapping[str, Mapping[str, bytes]], source: upstream.UpstreamSource
) -> dict[str, Mapping[str, bytes]]:
    return {name: files for name, files in skills.items() if name in source.skill_names}


_ANTI_SLOP_TAG = "v3.2.30"


def _client_for(
    tag: str,
    skills: Mapping[str, Mapping[str, bytes]],
    *,
    anti_slop_tag: str = _ANTI_SLOP_TAG,
) -> tuple[HttpClient, list[str]]:
    calls: list[str] = []
    superpowers = upstream.UNIVERSAL_UPSTREAMS[0]
    anti_slop = upstream.UNIVERSAL_UPSTREAMS[1]
    responses = {
        upstream.RELEASES_API_URL: (200, _release_body(tag)),
        upstream.ARCHIVE_URL_TEMPLATE.format(tag=tag): (
            200,
            _archive_bytes(
                tag, _source_skills(skills, superpowers), root=f"superpowers-{tag.lstrip('v')}"
            ),
        ),
        anti_slop.api_url: (200, _release_body(anti_slop_tag)),
        anti_slop.archive_url_template.format(tag=anti_slop_tag): (
            200,
            _archive_bytes(
                anti_slop_tag,
                _source_skills(skills, anti_slop),
                root=f"anti-slop-{anti_slop_tag.lstrip('v')}",
            ),
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
    marker = json.loads((skill_dir / upstream.MANAGED_MARKER_NAME).read_text(encoding="utf-8"))
    recorded = marker["files"]
    assert isinstance(recorded, dict)
    return {str(path): str(digest) for path, digest in recorded.items()}


def _managed_content_matches(skill_dir: Path, recorded: Mapping[str, str]) -> bool:
    actual = {
        path.relative_to(skill_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in skill_dir.rglob("*")
        if path.is_file() and path.name != upstream.MANAGED_MARKER_NAME
    }
    return actual == recorded


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


def test_a_publication_a_killed_process_left_half_finished_is_restored(sandbox: Path) -> None:
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


def test_an_orphan_staging_directory_is_reclaimed_by_the_next_update(sandbox: Path) -> None:
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


def test_a_missing_canonical_skill_is_reported_as_incomplete(sandbox: Path) -> None:
    root = _install_fixture_release()
    shutil.rmtree(root / "test-driven-development")
    client, calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert outcome.status == "incomplete"
    assert calls == [upstream.RELEASES_API_URL, upstream.UNIVERSAL_UPSTREAMS[1].api_url]
    assert any(
        "test-driven-development" in message and "missing" in message
        for message in outcome.messages
    ), outcome.messages


def test_a_user_edited_sibling_entry_is_preserved_by_the_repair(sandbox: Path) -> None:
    root = _install_fixture_release()
    entry = upstream.sibling_skills_roots()[0] / "test-driven-development"
    if entry.is_symlink():
        entry.unlink()
    shutil.copytree(root / "test-driven-development", entry)
    (entry / "SKILL.md").write_text("# my own edit\n", encoding="utf-8")
    client, _calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=client
    )

    assert (entry / "SKILL.md").read_text(encoding="utf-8") == "# my own edit\n"
    assert any("preserved" in message for message in outcome.messages), outcome.messages


def test_a_valid_release_installs_every_skill_and_mirrors_the_siblings(sandbox: Path) -> None:
    client, calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert outcome.installed == upstream.UNIVERSAL_SKILL_NAMES
    assert len(calls) == 4
    root = upstream.canonical_skills_root()
    for skill, files in _SKILL_FILES.items():
        for relative, data in files.items():
            assert (root / skill / relative).read_bytes() == data
    for sibling in upstream.sibling_skills_roots():
        for skill in upstream.UNIVERSAL_SKILL_NAMES:
            assert (sibling / skill / "SKILL.md").is_file()
            assert (sibling / skill / upstream.MANAGED_MARKER_NAME).is_file()
        for skill, files in _SKILL_FILES.items():
            for relative, data in files.items():
                assert (sibling / skill / relative).read_bytes() == data, (
                    f"{sibling}: the companion {relative} of {skill} must be readable"
                )


def test_upstream_mirror_regression_failed_early_root_does_not_skip_later_roots(
    sandbox: Path,
) -> None:
    sibling_roots = upstream.sibling_skills_roots()
    failed_root = sibling_roots[0]
    later_root = sibling_roots[1]
    failed_root.parent.mkdir(parents=True, exist_ok=True)
    failed_root.write_text("not a skill root directory\n", encoding="utf-8")
    client, _calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "incomplete"
    canonical_root = upstream.canonical_skills_root()
    for skill in upstream.UNIVERSAL_SKILL_NAMES:
        canonical_skill = canonical_root / skill
        later_skill = later_root / skill
        assert later_skill.is_dir()
        assert (later_skill / upstream.MANAGED_MARKER_NAME).is_file()
        for relative in _SKILL_FILES[skill]:
            assert (later_skill / relative).read_bytes() == (
                canonical_skill / relative
            ).read_bytes()
    assert any(
        str(failed_root) in message and upstream.UNIVERSAL_SKILL_NAMES[0] in message
        for message in outcome.messages
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
    assert _managed_content_matches(published, _managed_manifest(published))
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


def test_a_sibling_symlink_pointing_elsewhere_is_preserved_and_reported(sandbox: Path) -> None:
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


def test_preexisting_copy_in_alternate_agent_root_suppresses_install(sandbox: Path) -> None:
    alt_codex = sandbox / ".agents" / "skills" / "antislop"
    alt_codex.mkdir(parents=True)
    (alt_codex / "SKILL.md").write_text("# Pre-existing\n", encoding="utf-8")
    alt_agy = sandbox / ".gemini" / "config" / "skills" / "test-driven-development"
    alt_agy.mkdir(parents=True)
    (alt_agy / "SKILL.md").write_text("# Pre-existing\n", encoding="utf-8")
    client, _calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = upstream.maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "antislop" in outcome.preserved
    assert "test-driven-development" in outcome.preserved
    assert "systematic-debugging" in outcome.installed
    assert (alt_codex / "SKILL.md").read_text(encoding="utf-8") == "# Pre-existing\n"
    assert (alt_agy / "SKILL.md").read_text(encoding="utf-8") == "# Pre-existing\n"
    root = upstream.canonical_skills_root()
    assert not (root / "antislop").exists()
    assert not (root / "test-driven-development").exists()
