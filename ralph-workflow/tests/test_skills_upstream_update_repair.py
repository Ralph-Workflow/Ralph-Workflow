from __future__ import annotations

import shutil
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest

from ralph.skills.upstream import MAX_FILE_BYTES
from tests.test_skills_upstream_update import (
    _ANTI_SLOP,
    _NOW,
    _SKILL_FILES,
    RELEASES_API_URL,
    UNIVERSAL_SKILL_NAMES,
    UpdateOutcome,
    _archive_bytes,
    _archive_url,
    _canonical_root,
    _client_for,
    _fake_client,
    _member,
    _release_body,
    _sibling_roots,
    _skill_members,
    _tree_snapshot,
    maybe_update_universal_skills,
)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


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
    assert repair_calls == [RELEASES_API_URL, _ANTI_SLOP.api_url], (
        "the repair must not fetch the archive again"
    )
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
