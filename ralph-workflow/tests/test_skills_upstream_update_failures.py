from __future__ import annotations

import hashlib
import io
import json
import tarfile
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
    UpstreamSource,
    canonical_skills_root,
    maybe_update_universal_skills,
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
    skills: Mapping[str, Mapping[str, bytes]], source: UpstreamSource
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


def _release_body_with_raw_tag(value: object) -> bytes:
    return json.dumps({"tag_name": value}).encode("utf-8")


def _refused_tag_client(body: bytes) -> tuple[HttpClient, list[str]]:
    calls: list[str] = []
    return _fake_client({RELEASES_API_URL: (200, body)}, calls), calls


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
