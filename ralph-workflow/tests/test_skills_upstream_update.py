"""Behavioural tests for the universal upstream skill updater.

The upstream seam is injected through the public ``http`` parameter, so the
whole path is exercised without a network socket: a release payload, the
archive bytes, the archive validation, the install into the user-global
roots and the marker that records the installed digests.

Every filesystem path the updater touches lives under ``tmp_path``, and the
fixture points ``HOME`` and ``XDG_CACHE_HOME`` at that sandbox, so the
canonical root, the sibling roots, the attempt cache and the installed-tag
state file are all isolated from the developer's real home.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.skills.upstream import (
    ARCHIVE_URL_TEMPLATE,
    MAX_ARCHIVE_BYTES,
    RELEASES_API_URL,
    UNIVERSAL_SKILL_NAMES,
    HttpClient,
    HttpResponse,
    UpdateOutcome,
    attempt_cache_path,
    canonical_skills_root,
    maybe_update_universal_skills,
    sibling_skills_roots,
    universal_state_path,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_NOW = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)

_SKILL_FILES: dict[str, dict[str, bytes]] = {
    "test-driven-development": {
        "SKILL.md": b"# Test driven development\n\nWrite the failing test first.\n",
        "test-first.md": b"# Test first\n",
    },
    "systematic-debugging": {
        "SKILL.md": b"# Systematic debugging\n\nSee `condition-based-waiting-example.ts`.\n",
        "root-cause-tracing.md": b"# Root cause tracing\n",
        "defense-in-depth.md": b"# Defense in depth\n",
        "condition-based-waiting.md": (
            b"# Condition based waiting\n\nSee `condition-based-waiting-example.ts` in this directory.\n"
        ),
        "condition-based-waiting-example.ts": b"export const wait = (): boolean => true;\n",
        "find-polluter.sh": b"#!/bin/sh\necho polluter\n",
    },
    "verification-before-completion": {
        "SKILL.md": b"# Verification before completion\n",
        "evidence-checklist.md": b"# Evidence checklist\n",
    },
}


def _canonical_root() -> Path:
    """Return the canonical user-global skill root."""
    return canonical_skills_root()


def _sibling_roots() -> tuple[Path, ...]:
    """Return the registered sibling user-global skill roots."""
    return sibling_skills_roots()


def _release_body(tag: str) -> bytes:
    """Return the release payload the upstream API answers with."""
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _archive_url(tag: str) -> str:
    """Return the archive URL the updater requests for ``tag``."""
    return ARCHIVE_URL_TEMPLATE.format(tag=tag)


def _member(name: str, data: bytes, *, mode: int = 0o644) -> tarfile.TarInfo:
    """Return a regular-file member carrying ``data``."""
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    return info


def _skill_members(
    tag: str,
    skills: Mapping[str, Mapping[str, bytes]],
    *,
    top_dir: str | None = None,
) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    """Return the members of the three universal skills for ``tag``.

    The archive directory name follows the upstream spelling: the leading
    ``v`` of the tag is absent from the directory name.
    """
    root = top_dir if top_dir is not None else f"superpowers-{tag.lstrip('v')}"
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    for skill, files in skills.items():
        for relative, data in files.items():
            mode = 0o755 if relative.endswith(".sh") else 0o644
            members.append((_member(f"{root}/skills/{skill}/{relative}", data, mode=mode), data))
    return members


def _archive_bytes(members: Sequence[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    """Return the archive bytes for ``members``."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for info, data in members:
            if data is None:
                archive.addfile(info)
            else:
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _release_archive(tag: str, skills: Mapping[str, Mapping[str, bytes]]) -> bytes:
    """Return the archive bytes of a release carrying ``skills``."""
    return _archive_bytes(_skill_members(tag, skills))


def _fake_client(
    responses: Mapping[str, HttpResponse],
    calls: list[str],
    *,
    error: Exception | None = None,
) -> HttpClient:
    """Return a fake HTTP client that answers from ``responses``."""

    def _get(url: str, timeout: float) -> HttpResponse:
        calls.append(url)
        if error is not None:
            raise error
        return responses.get(url, (404, b""))

    return _get


def _client_for(
    tag: str, skills: Mapping[str, Mapping[str, bytes]]
) -> tuple[HttpClient, list[str]]:
    """Return a fake client serving one release, plus its request log."""
    calls: list[str] = []
    responses = {
        RELEASES_API_URL: (200, _release_body(tag)),
        _archive_url(tag): (200, _release_archive(tag, skills)),
    }
    return (_fake_client(responses, calls), calls)


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


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the user-global roots, the cache and the state file at a sandbox."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


def test_fresh_install_materializes_every_companion_file(sandbox: Path) -> None:
    client, calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert outcome.installed == UNIVERSAL_SKILL_NAMES
    assert outcome.preserved == ()
    assert outcome.attempted is True
    assert outcome.tag == "v-test-1"
    assert calls == [RELEASES_API_URL, _archive_url("v-test-1")]
    root = _canonical_root()
    for skill, files in _SKILL_FILES.items():
        for relative, data in files.items():
            assert (root / skill / relative).read_bytes() == data
    script = root / "systematic-debugging" / "find-polluter.sh"
    assert script.stat().st_mode & 0o111


def test_the_archive_layout_without_the_leading_v_is_accepted(sandbox: Path) -> None:
    members = _skill_members("v-test-1", _SKILL_FILES, top_dir="superpowers-test-1")
    client = _fake_client(
        {
            RELEASES_API_URL: (200, _release_body("v-test-1")),
            _archive_url("v-test-1"): (200, _archive_bytes(members)),
        },
        [],
    )

    outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    root = _canonical_root()
    assert (root / "test-driven-development" / "SKILL.md").is_file()


def test_the_marker_matches_the_frozen_shape(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)

    maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    skill_dir = _canonical_root() / "test-driven-development"
    marker = json.loads((skill_dir / ".ralph-managed.json").read_text(encoding="utf-8"))
    assert set(marker) == {
        "managed_by",
        "source",
        "source_tag",
        "installed_sha256",
        "files",
    }
    assert marker["managed_by"] == "ralph-universal"
    assert marker["source"] == "obra/superpowers"
    assert marker["source_tag"] == "v-test-1"
    files = marker["files"]
    assert set(files) == {"SKILL.md", "test-first.md"}
    for relative, digest in files.items():
        expected = hashlib.sha256((skill_dir / relative).read_bytes()).hexdigest()
        assert digest == expected
    manifest = json.dumps(dict(sorted(files.items())), separators=(",", ":"), sort_keys=True)
    assert marker["installed_sha256"] == hashlib.sha256(manifest.encode("utf-8")).hexdigest()


def test_the_sibling_roots_mirror_the_installed_skills(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)

    maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    for sibling in _sibling_roots():
        for skill in UNIVERSAL_SKILL_NAMES:
            assert (sibling / skill / "SKILL.md").is_file()
            assert (sibling / skill / ".ralph-managed.json").is_file()


def test_the_upgrade_installs_the_changed_companion_set(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    project = tmp_path / "project"
    project.mkdir()
    (project / "keep.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.chdir(project)
    before = _tree_snapshot(project)

    changed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    changed["systematic-debugging"].pop("root-cause-tracing.md")
    changed["systematic-debugging"]["new-companion.md"] = b"new companion\n"
    changed["systematic-debugging"]["SKILL.md"] = b"# Systematic debugging v2\n"
    upgrade_client, calls = _client_for("v-test-2", changed)

    outcome = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=upgrade_client
    )

    assert outcome.status == "updated"
    assert outcome.tag == "v-test-2"
    assert len(calls) == 2
    assert _tree_snapshot(project) == before
    root = _canonical_root()
    assert not (root / "systematic-debugging" / "root-cause-tracing.md").exists()
    assert (root / "systematic-debugging" / "new-companion.md").read_bytes() == b"new companion\n"
    assert (
        root / "systematic-debugging" / "SKILL.md"
    ).read_bytes() == b"# Systematic debugging v2\n"


@pytest.mark.parametrize(
    "failure",
    ["release-500", "release-timeout", "garbage-archive", "oversize-archive", "denied-extension"],
)
def test_a_failing_release_keeps_the_installed_skills_byte_identical(
    sandbox: Path, failure: str
) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    root = _canonical_root()
    before = _tree_snapshot(root)

    failing = _failing_client(failure)
    outcome = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=failing
    )

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert _tree_snapshot(root) == before


def test_a_symlink_member_keeps_the_installed_skills_byte_identical(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    root = _canonical_root()
    before = _tree_snapshot(root)

    members = _skill_members("v-test-2", _SKILL_FILES)
    link = tarfile.TarInfo("superpowers-test-2/skills/test-driven-development/escape.md")
    link.type = tarfile.SYMTYPE
    link.linkname = "../../../../etc/passwd"
    members.append((link, None))
    failing = _fake_client(
        {
            RELEASES_API_URL: (200, _release_body("v-test-2")),
            _archive_url("v-test-2"): (200, _archive_bytes(members)),
        },
        [],
    )

    outcome = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=failing
    )

    assert outcome.status == "failed"
    assert _tree_snapshot(root) == before


def _failing_client(failure: str) -> HttpClient:
    """Return a client that reproduces one upstream failure mode."""
    calls: list[str] = []
    if failure == "release-500":
        return _fake_client({RELEASES_API_URL: (500, b"upstream failure")}, calls)
    if failure == "release-timeout":
        return _fake_client({}, calls, error=TimeoutError("the upstream did not answer"))
    if failure == "garbage-archive":
        return _fake_client(
            {
                RELEASES_API_URL: (200, _release_body("v-test-2")),
                _archive_url("v-test-2"): (200, b"this is not a tarball"),
            },
            calls,
        )
    if failure == "oversize-archive":
        oversized = b"\0" * (MAX_ARCHIVE_BYTES + 1)
        return _fake_client(
            {
                RELEASES_API_URL: (200, _release_body("v-test-2")),
                _archive_url("v-test-2"): (200, oversized),
            },
            calls,
        )
    members = _skill_members("v-test-2", _SKILL_FILES)
    members.append(
        (_member("superpowers-test-2/skills/test-driven-development/diagram.png", b"x"), b"x")
    )
    return _fake_client(
        {
            RELEASES_API_URL: (200, _release_body("v-test-2")),
            _archive_url("v-test-2"): (200, _archive_bytes(members)),
        },
        calls,
    )


def test_the_disabled_switch_makes_no_request_and_writes_nothing(
    sandbox: Path, tmp_path: Path
) -> None:
    client, calls = _client_for("v-test-1", _SKILL_FILES)
    before = _tree_snapshot(tmp_path)

    outcome = maybe_update_universal_skills(enabled=False, now=_NOW, http=client)

    assert outcome.status == "disabled"
    assert outcome.attempted is False
    assert calls == []
    assert _tree_snapshot(tmp_path) == before


def test_a_user_edited_skill_is_preserved_and_reported(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    edited = _canonical_root() / "systematic-debugging" / "SKILL.md"
    edited.write_text("# my own debugging notes\n", encoding="utf-8")
    changed = {name: dict(files) for name, files in _SKILL_FILES.items()}
    changed["systematic-debugging"]["SKILL.md"] = b"# Systematic debugging v2\n"
    upgrade_client, _calls = _client_for("v-test-2", changed)

    outcome = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=upgrade_client
    )

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    assert edited.read_text(encoding="utf-8") == "# my own debugging notes\n"
    assert any("systematic-debugging" in message for message in outcome.messages)


def test_two_invocations_within_a_day_make_one_attempt(sandbox: Path) -> None:
    client, calls = _client_for("v-test-1", _SKILL_FILES)

    first = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    snapshot = _tree_snapshot(_canonical_root())
    second = maybe_update_universal_skills(enabled=True, now=_NOW + timedelta(hours=1), http=client)

    assert first.status == "updated"
    assert second.status == "skipped"
    assert second.attempted is False
    assert len(calls) == 2
    assert _tree_snapshot(_canonical_root()) == snapshot


def test_concurrent_starts_make_one_attempt_and_keep_the_cache_parseable(
    sandbox: Path,
) -> None:
    client, calls = _client_for("v-test-1", _SKILL_FILES)
    outcomes: list[UpdateOutcome] = []
    barrier = threading.Barrier(2)

    def _worker() -> None:
        barrier.wait(timeout=5.0)
        outcomes.append(maybe_update_universal_skills(enabled=True, now=_NOW, http=client))

    threads = [threading.Thread(target=_worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(calls) == 2
    assert sum(1 for outcome in outcomes if outcome.attempted) == 1
    document = json.loads(attempt_cache_path().read_text(encoding="utf-8"))
    assert "last_attempt" in document


def test_an_invalid_cache_and_a_malformed_state_are_treated_as_absent(sandbox: Path) -> None:
    cache = attempt_cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text("this is not json", encoding="utf-8")
    state = universal_state_path()
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("{ not json either", encoding="utf-8")
    client, calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert len(calls) == 2


def test_a_skill_referencing_a_missing_companion_is_not_installed(sandbox: Path) -> None:
    incomplete = {name: dict(files) for name, files in _SKILL_FILES.items()}
    incomplete["systematic-debugging"].pop("condition-based-waiting-example.ts")
    client, _calls = _client_for("v-test-1", incomplete)

    outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert "systematic-debugging" in outcome.preserved
    assert "test-driven-development" in outcome.installed
    root = _canonical_root()
    assert not (root / "systematic-debugging" / "SKILL.md").exists()
    assert any("condition-based-waiting-example.ts" in message for message in outcome.messages)


def test_no_write_lands_outside_the_allowed_set(sandbox: Path, tmp_path: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)

    maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    state = universal_state_path()
    cache = attempt_cache_path()
    skill_roots = (_canonical_root(), *_sibling_roots())
    for path in sorted(tmp_path.rglob("*")):
        if path.is_symlink():
            target = path.readlink().resolve()
            assert any(root == target or root in target.parents for root in skill_roots), path
            continue
        if not path.is_file():
            continue
        if path in (state, cache):
            continue
        if cache.parent in path.parents:
            continue
        assert any(root in path.parents for root in skill_roots), path
