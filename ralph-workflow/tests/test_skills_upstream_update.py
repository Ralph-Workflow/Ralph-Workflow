from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import threading
import tracemalloc
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.skills import upstream
from ralph.skills.upstream import (
    ARCHIVE_URL_TEMPLATE,
    MAX_ARCHIVE_BYTES,
    MAX_FILE_BYTES,
    RELEASES_API_URL,
    UNIVERSAL_SKILL_NAMES,
    UNIVERSAL_UPSTREAMS,
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

_SUPERPOWERS = UNIVERSAL_UPSTREAMS[0]

_ANTI_SLOP = UNIVERSAL_UPSTREAMS[1]

_ANTI_SLOP_TAG = "v3.2.30"

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
    "antislop": {"SKILL.md": b"# Antislop\n\nFilter AI slop.\n", "VERSION": b"3.2.30\n"},
}


def _canonical_root() -> Path:
    return canonical_skills_root()


def _sibling_roots() -> tuple[Path, ...]:
    return sibling_skills_roots()


def _release_body(tag: str) -> bytes:
    return json.dumps({"tag_name": tag}).encode("utf-8")


def _archive_url(tag: str) -> str:
    return ARCHIVE_URL_TEMPLATE.format(tag=tag)


def _member(name: str, data: bytes, *, mode: int = 0o644) -> tarfile.TarInfo:
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
    root = top_dir if top_dir is not None else f"superpowers-{tag.lstrip('v')}"
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    for skill, files in skills.items():
        for relative, data in files.items():
            mode = 0o755 if relative.endswith(".sh") else 0o644
            members.append((_member(f"{root}/skills/{skill}/{relative}", data, mode=mode), data))
    return members


def _archive_bytes(members: Sequence[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for info, data in members:
            if data is None:
                archive.addfile(info)
            else:
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _release_archive(
    tag: str, skills: Mapping[str, Mapping[str, bytes]], *, top_dir: str | None = None
) -> bytes:
    return _archive_bytes(_skill_members(tag, skills, top_dir=top_dir))


def _bulk_members(tag: str, count: int, size: int) -> list[tuple[tarfile.TarInfo, bytes | None]]:
    root = f"superpowers-{tag.lstrip('v')}"
    members: list[tuple[tarfile.TarInfo, bytes | None]] = []
    for index in range(count):
        data = b"a" * size
        members.append(
            (
                _member(f"{root}/skills/test-driven-development/bulk-{index:04d}.md", data),
                data,
            )
        )
    return members


def _fake_client(
    responses: Mapping[str, HttpResponse], calls: list[str], *, error: Exception | None = None
) -> HttpClient:
    def _get(url: str, timeout: float) -> HttpResponse:
        calls.append(url)
        if error is not None:
            raise error
        return responses.get(url, (404, b""))

    return _get


def _source_skills(
    skills: Mapping[str, Mapping[str, bytes]], source: upstream.UpstreamSource
) -> dict[str, Mapping[str, bytes]]:
    return {name: files for name, files in skills.items() if name in source.skill_names}


def _client_for(
    tag: str,
    skills: Mapping[str, Mapping[str, bytes]],
    *,
    anti_slop_tag: str | None = None,
) -> tuple[HttpClient, list[str]]:
    anti_tag = tag if anti_slop_tag is None else anti_slop_tag
    calls: list[str] = []
    responses = {
        RELEASES_API_URL: (200, _release_body(tag)),
        _archive_url(tag): (200, _release_archive(tag, _source_skills(skills, _SUPERPOWERS))),
        _ANTI_SLOP.api_url: (200, _release_body(anti_tag)),
        _ANTI_SLOP.archive_url_template.format(tag=anti_tag): (
            200,
            _release_archive(
                anti_tag,
                _source_skills(skills, _ANTI_SLOP),
                top_dir=f"anti-slop-{anti_tag.lstrip('v')}",
            ),
        ),
    }
    return (_fake_client(responses, calls), calls)


def _anti_slop_archive_url(tag: str) -> str:
    return _ANTI_SLOP.archive_url_template.format(tag=tag)


def _tree_snapshot(root: Path) -> dict[str, str]:
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[rel] = "symlink:" + path.readlink().as_posix()
        elif path.is_file():
            snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return snapshot


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
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
    assert calls == [
        RELEASES_API_URL,
        _archive_url("v-test-1"),
        _ANTI_SLOP.api_url,
        _anti_slop_archive_url("v-test-1"),
    ]
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
    assert set(marker) == {"files", "installed_sha256", "managed_by", "source", "source_tag"}
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
        for skill, files in _SKILL_FILES.items():
            for relative, data in files.items():
                assert (sibling / skill / relative).read_bytes() == data, (
                    f"{sibling}: the companion {relative} of {skill} must be readable"
                )


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
    assert len(calls) == 4
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
    calls: list[str] = []
    if failure == "release-500":
        return _fake_client({RELEASES_API_URL: (500, b"upstream failure")}, calls)
    if failure == "release-timeout":
        return _fake_client({}, calls, error=TimeoutError("the upstream did not answer"))
    rel = (200, _release_body("v-test-2"))
    url = _archive_url("v-test-2")
    if failure == "garbage-archive":
        return _fake_client({RELEASES_API_URL: rel, url: (200, b"this is not a tarball")}, calls)
    if failure == "oversize-archive":
        return _fake_client(
            {RELEASES_API_URL: rel, url: (200, b"\0" * (MAX_ARCHIVE_BYTES + 1))}, calls
        )
    members = _skill_members("v-test-2", _SKILL_FILES)
    members.append(
        (_member("superpowers-test-2/skills/test-driven-development/diagram.png", b"x"), b"x")
    )
    return _fake_client({RELEASES_API_URL: rel, url: (200, _archive_bytes(members))}, calls)


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
    assert len(calls) == 4
    assert _tree_snapshot(_canonical_root()) == snapshot


@pytest.mark.parametrize(
    "failure",
    ["release-500", "release-timeout", "garbage-archive", "oversize-archive", "denied-extension"],
)
def test_a_failed_attempt_keeps_the_skills_and_schedules_a_retry(
    sandbox: Path, failure: str
) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    assert maybe_update_universal_skills(enabled=True, now=_NOW, http=client).status == "updated"
    root = _canonical_root()
    before = _tree_snapshot(root)

    failed = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=_failing_client(failure)
    )

    assert failed.status == "failed"
    assert _tree_snapshot(root) == before, "a failed attempt must keep the installed skills"

    retry_client, retry_calls = _client_for("v-test-2", _SKILL_FILES)
    skipped = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=26), http=retry_client
    )

    assert skipped.status == "skipped"
    assert skipped.attempted is False
    assert retry_calls == [], "a failed attempt must not be retried inside the 24 h window"

    later_client, later_calls = _client_for("v-test-2", _SKILL_FILES)
    retried = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=49, minutes=1), http=later_client
    )

    assert retried.status == "updated"
    assert later_calls == [
        RELEASES_API_URL,
        _archive_url("v-test-2"),
        _ANTI_SLOP.api_url,
        _anti_slop_archive_url("v-test-2"),
    ]


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

    assert len(calls) == 4
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
    assert len(calls) == 4


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


def _release_body_with_raw_tag(value: object) -> bytes:
    return json.dumps({"tag_name": value}).encode("utf-8")


def _refused_tag_client(body: bytes) -> tuple[HttpClient, list[str]]:
    calls: list[str] = []
    return _fake_client({RELEASES_API_URL: (200, body)}, calls), calls


def test_a_traversal_tag_is_refused_before_any_archive_request(sandbox: Path) -> None:
    client, calls = _refused_tag_client(_release_body("../../../../evil/repo"))

    outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "failed"
    assert outcome.error == "release-api-malformed"
    assert calls == [RELEASES_API_URL, _ANTI_SLOP.api_url]
    root = _canonical_root()
    for skill in UNIVERSAL_SKILL_NAMES:
        assert not (root / skill).exists()


@pytest.mark.parametrize(
    "tag",
    ["release/next", "..", ".", "v1.2.3 rc.1", "https://evil.example/repo", "v1.2.3\\next"],
)
def test_a_hostile_tag_shape_is_refused_before_any_archive_request(sandbox: Path, tag: str) -> None:
    client, calls = _refused_tag_client(_release_body(tag))

    outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "failed"
    assert outcome.error == "release-api-malformed"
    assert calls == [RELEASES_API_URL, _ANTI_SLOP.api_url]
    root = _canonical_root()
    for skill in UNIVERSAL_SKILL_NAMES:
        assert not (root / skill).exists()


def test_a_realistic_release_tag_is_still_accepted(sandbox: Path) -> None:
    tag = "v1.2.3-rc.1"
    client, calls = _client_for(tag, _SKILL_FILES)

    outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    assert outcome.tag == tag
    assert calls == [
        RELEASES_API_URL,
        _archive_url(tag),
        _ANTI_SLOP.api_url,
        _anti_slop_archive_url(tag),
    ]
    expected = "https://codeload.github.com/obra/superpowers/tar.gz/refs/tags/v1.2.3-rc.1"
    assert calls[1] == expected
    assert outcome.installed == UNIVERSAL_SKILL_NAMES


@pytest.mark.parametrize("value", [42, None, "", ["v1.2.3"]])
def test_a_malformed_tag_name_is_refused_before_any_archive_request(
    sandbox: Path, value: object
) -> None:
    client, calls = _refused_tag_client(_release_body_with_raw_tag(value))

    outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "failed"
    assert outcome.error == "release-api-malformed"
    assert calls == [RELEASES_API_URL, _ANTI_SLOP.api_url]
    root = _canonical_root()
    for skill in UNIVERSAL_SKILL_NAMES:
        assert not (root / skill).exists()


def test_a_refused_tag_keeps_the_installed_skills_byte_identical(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
    root = _canonical_root()
    before = _tree_snapshot(root)

    hostile, calls = _refused_tag_client(_release_body("../../../../evil/repo"))
    outcome = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=hostile
    )

    assert outcome.status == "failed"
    assert outcome.error == "release-api-malformed"
    assert calls == [RELEASES_API_URL, _ANTI_SLOP.api_url]
    assert _tree_snapshot(root) == before


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


def test_an_archive_beyond_the_extracted_size_cap_never_materialises_its_content(
    sandbox: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(upstream, "MAX_EXTRACTED_BYTES", 1024 * 1024)
    members = _skill_members("v-test-1", _SKILL_FILES)
    members.extend(_bulk_members("v-test-1", 20, 1024 * 1024))
    client = _fake_client(
        {
            RELEASES_API_URL: (200, _release_body("v-test-1")),
            _archive_url("v-test-1"): (200, _archive_bytes(members)),
        },
        [],
    )

    tracemalloc.start()
    try:
        outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert outcome.status == "failed"
    assert peak < 8 * 1024 * 1024, (
        f"the extracted-size cap must bound the materialised content; peak was {peak} bytes"
    )


def test_only_the_release_api_and_the_tag_archive_are_fetched(sandbox: Path) -> None:
    client, calls = _client_for("v-test-1", _SKILL_FILES)

    maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert calls == [
        RELEASES_API_URL,
        _archive_url("v-test-1"),
        _ANTI_SLOP.api_url,
        _anti_slop_archive_url("v-test-1"),
    ]
    assert calls[0] == "https://api.github.com/repos/obra/superpowers/releases/latest"
    assert calls[1].startswith("https://codeload.github.com/obra/superpowers/tar.gz/refs/tags/")
    for url in calls:
        assert "refs/heads" not in url, url
        assert "main" not in url, url


def test_every_user_global_root_receives_the_installed_skills(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "keep.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.chdir(project)
    before = _tree_snapshot(project)
    client, _calls = _client_for("v-test-1", _SKILL_FILES)

    outcome = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert outcome.status == "updated"
    roots = {
        agent: Path.home().joinpath(*segments).resolve()
        for agent, segments in (
            ("claude", (".claude", "skills")),
            ("codex", (".codex", "skills")),
            ("opencode", (".config", "opencode", "skills")),
            ("agy", (".gemini", "antigravity-cli", "skills")),
            ("codex-agents", (".agents", "skills")),
            ("agy-config", (".gemini", "config", "skills")),
        )
    }
    assert roots["codex-agents"] == (sandbox / ".agents" / "skills").resolve()
    for agent, root in roots.items():
        for skill in UNIVERSAL_SKILL_NAMES:
            assert (root / skill / "SKILL.md").is_file(), f"{agent}: {skill} is missing"
        for skill, files in _SKILL_FILES.items():
            for relative, data in files.items():
                assert (root / skill / relative).read_bytes() == data, (
                    f"{agent}: the companion {relative} of {skill} must be readable"
                )
    assert _tree_snapshot(project) == before, "the update must not write into the project"


def test_a_skill_missing_from_the_archive_is_left_unchanged_and_reported(
    sandbox: Path,
) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    assert maybe_update_universal_skills(enabled=True, now=_NOW, http=client).status == "updated"
    root = _canonical_root()
    missing = "verification-before-completion"
    installed = (root / missing / "SKILL.md").read_bytes()
    partial = {name: dict(files) for name, files in _SKILL_FILES.items()}
    partial.pop(missing)
    upgrade_client, _calls = _client_for("v-test-2", partial)

    outcome = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=upgrade_client
    )

    assert outcome.status == "updated"
    assert missing in outcome.preserved
    assert missing not in outcome.installed
    assert any(missing in message for message in outcome.messages)
    assert (root / missing / "SKILL.md").read_bytes() == installed


@pytest.mark.parametrize("status", [403, 429])
def test_a_refused_release_api_keeps_the_installed_skills(sandbox: Path, status: int) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    assert maybe_update_universal_skills(enabled=True, now=_NOW, http=client).status == "updated"
    root = _canonical_root()
    before = _tree_snapshot(root)
    calls: list[str] = []
    refused = _fake_client({RELEASES_API_URL: (status, b"upstream refused")}, calls)

    outcome = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=refused
    )

    assert outcome.status == "failed"
    assert outcome.error == f"release-api-status-{status}"
    assert calls == [RELEASES_API_URL, _ANTI_SLOP.api_url]
    assert _tree_snapshot(root) == before


def test_a_truncated_archive_keeps_the_installed_skills(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    assert maybe_update_universal_skills(enabled=True, now=_NOW, http=client).status == "updated"
    root = _canonical_root()
    before = _tree_snapshot(root)
    archive = _release_archive("v-test-2", _SKILL_FILES)
    truncated = archive[: len(archive) // 2]
    failing = _fake_client(
        {
            RELEASES_API_URL: (200, _release_body("v-test-2")),
            _archive_url("v-test-2"): (200, truncated),
        },
        [],
    )

    outcome = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=failing
    )

    assert outcome.status == "failed"
    assert outcome.error is not None
    assert _tree_snapshot(root) == before


def test_an_oversize_archive_is_refused(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    assert maybe_update_universal_skills(enabled=True, now=_NOW, http=client).status == "updated"
    root = _canonical_root()
    before = _tree_snapshot(root)
    failing = _fake_client(
        {
            RELEASES_API_URL: (200, _release_body("v-test-2")),
            _archive_url("v-test-2"): (200, b"\0" * (MAX_ARCHIVE_BYTES + 1)),
        },
        [],
    )

    outcome = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=failing
    )

    assert outcome.status == "failed"
    assert outcome.error == "archive-too-large"
    assert _tree_snapshot(root) == before


def test_a_member_with_a_refused_extension_is_not_installed(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    assert maybe_update_universal_skills(enabled=True, now=_NOW, http=client).status == "updated"
    root = _canonical_root()
    before = _tree_snapshot(root)
    members = _skill_members("v-test-2", _SKILL_FILES)
    members.append(
        (_member("superpowers-test-2/skills/test-driven-development/diagram.png", b"x"), b"x")
    )
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
    assert outcome.error is not None
    assert _tree_snapshot(root) == before


def test_a_member_path_that_escapes_the_skill_directory_is_refused(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    assert maybe_update_universal_skills(enabled=True, now=_NOW, http=client).status == "updated"
    root = _canonical_root()
    before = _tree_snapshot(root)
    members = _skill_members("v-test-2", _SKILL_FILES)
    members.append(
        (
            _member(
                "superpowers-test-2/skills/test-driven-development/../../../escape.md",
                b"x",
            ),
            b"x",
        )
    )
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
    assert outcome.error is not None
    assert _tree_snapshot(root) == before
    assert not (sandbox / "escape.md").exists()


def _block_root(root: Path) -> None:
    if root.is_dir():
        shutil.rmtree(root)
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text("the root cannot hold a skill\n", encoding="utf-8")


def _unblock_root(root: Path) -> None:
    root.unlink()


def _assert_every_root_carries_every_skill() -> None:
    for root in (_canonical_root(), *_sibling_roots()):
        for skill in UNIVERSAL_SKILL_NAMES:
            assert (root / skill / "SKILL.md").is_file(), f"{root}: {skill} is missing"
        for skill, files in _SKILL_FILES.items():
            for relative, data in files.items():
                assert (root / skill / relative).read_bytes() == data, (
                    f"{root}: the companion {relative} of {skill} must be readable"
                )


def test_a_transient_publication_failure_is_repaired_by_a_later_run(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "keep.txt").write_text("keep\n", encoding="utf-8")
    monkeypatch.chdir(project)
    before = _tree_snapshot(project)
    blocked_root = _sibling_roots()[-1]
    _block_root(blocked_root)
    client, _first_calls = _client_for("v-test-1", _SKILL_FILES)

    first = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert first.status == "incomplete"
    assert first.error is None
    assert any("failed" in message for message in first.messages), first.messages
    for root in _sibling_roots()[:-1]:
        for skill in UNIVERSAL_SKILL_NAMES:
            assert (root / skill / "SKILL.md").is_file(), f"{root}: {skill} is missing"
    for skill in UNIVERSAL_SKILL_NAMES:
        assert not (blocked_root / skill).exists()

    _unblock_root(blocked_root)
    repair_client, repair_calls = _client_for("v-test-1", _SKILL_FILES)

    repaired = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=repair_client
    )

    assert repaired.status != "up-to-date"
    assert repaired.tag == "v-test-1"
    assert repair_calls == [RELEASES_API_URL, _ANTI_SLOP.api_url], "the repair must not fetch the archive again"
    _assert_every_root_carries_every_skill()
    assert _tree_snapshot(project) == before, "the repair must not write into the project"

    settled_client, settled_calls = _client_for("v-test-1", _SKILL_FILES)

    settled = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=49), http=settled_client
    )

    assert settled.status == "up-to-date"
    assert settled_calls == [RELEASES_API_URL, _ANTI_SLOP.api_url]


def test_a_user_owned_sibling_entry_is_preserved_by_the_repair(
    sandbox: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    sibling_root = _sibling_roots()[0]
    own = sibling_root / "test-driven-development"
    own.mkdir(parents=True)
    (own / "SKILL.md").write_text("# my own skill\n", encoding="utf-8")
    client, _calls = _client_for("v-test-1", _SKILL_FILES)

    first = maybe_update_universal_skills(enabled=True, now=_NOW, http=client)

    assert first.status == "updated"
    assert (own / "SKILL.md").read_text(encoding="utf-8") == "# my own skill\n"
    assert any(
        "test-driven-development" in message and "preserved" in message
        for message in first.messages
    ), first.messages
    repair_client, repair_calls = _client_for("v-test-1", _SKILL_FILES)

    repaired = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=repair_client
    )

    assert (own / "SKILL.md").read_text(encoding="utf-8") == "# my own skill\n"
    assert repair_calls == [
        RELEASES_API_URL,
        _archive_url("v-test-1"),
        _ANTI_SLOP.api_url,
    ]
    assert any(
        "test-driven-development" in message and "preserved" in message
        for message in repaired.messages
    ), repaired.messages


def _attempt_with_hostile_member(
    member: tuple[tarfile.TarInfo, bytes],
) -> tuple[UpdateOutcome, dict[str, str]]:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    assert maybe_update_universal_skills(enabled=True, now=_NOW, http=client).status == "updated"
    before = _tree_snapshot(_canonical_root())
    members = _skill_members("v-test-2", _SKILL_FILES)
    members.append(member)
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

    return outcome, before


def test_a_member_with_an_absolute_path_is_refused(sandbox: Path) -> None:
    outcome, before = _attempt_with_hostile_member(
        (_member(str(sandbox / "absolute.md"), b"x"), b"x")
    )

    assert outcome.status == "failed"
    assert outcome.error == "the archive carries an absolute member path"
    assert _tree_snapshot(_canonical_root()) == before
    assert not (sandbox / "absolute.md").exists()


def test_a_file_above_the_size_cap_is_refused(sandbox: Path) -> None:
    oversize = b"a" * (MAX_FILE_BYTES + 1)
    outcome, before = _attempt_with_hostile_member(
        (
            _member("superpowers-test-2/skills/test-driven-development/oversize.md", oversize),
            oversize,
        )
    )

    assert outcome.status == "failed"
    assert outcome.error == "the archive carries a file above the size cap"
    assert _tree_snapshot(_canonical_root()) == before
    assert not (_canonical_root() / "test-driven-development" / "oversize.md").exists()


def test_single_source_state_file_is_migrated_in_place_without_reinstall(sandbox: Path) -> None:
    client, _calls = _client_for("v-test-1", _SKILL_FILES)
    assert maybe_update_universal_skills(enabled=True, now=_NOW, http=client).status == "updated"
    state_path = universal_state_path()
    old_skills = {
        n: {"source_tag": "v-test-1", "installed_sha256": "0" * 64} for n in UNIVERSAL_SKILL_NAMES
    }
    state_path.write_text(
        json.dumps(
            {"source": "obra/superpowers", "installed_tag": "v-test-1", "skills": old_skills}
        ),
        encoding="utf-8",
    )
    check_client, check_calls = _client_for("v-test-1", _SKILL_FILES)
    second = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=25), http=check_client
    )
    assert second.status == "up-to-date"
    assert check_calls == [RELEASES_API_URL, _ANTI_SLOP.api_url]
    migrated = json.loads(state_path.read_text(encoding="utf-8"))
    assert "source" not in migrated and "installed_tag" not in migrated
    assert (
        migrated.get("sources", {}).get("obra/superpowers", {}).get("installed_tag") == "v-test-1"
    )
    assert upstream._needs_update("v-test-1", migrated) is False
    assert upstream._needs_update("v-test-2", migrated) is True
