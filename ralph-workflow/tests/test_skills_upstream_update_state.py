from __future__ import annotations

import json
import tarfile
from datetime import timedelta
from pathlib import Path

import pytest

from ralph.skills.upstream import (
    MAX_FILE_BYTES,
    RELEASES_API_URL,
    UNIVERSAL_SKILL_NAMES,
    UpdateOutcome,
    maybe_update_universal_skills,
    universal_state_path,
)
from tests.test_skills_upstream_update import (
    _ANTI_SLOP,
    _NOW,
    _SKILL_FILES,
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
)


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
    assert second.tag == "v-test-1"
    changed_client, changed_calls = _client_for("v-test-2", _SKILL_FILES)
    changed = maybe_update_universal_skills(
        enabled=True, now=_NOW + timedelta(hours=49), http=changed_client
    )
    assert changed.status == "updated"
    assert changed.tag == "v-test-2"
    assert len(changed_calls) == 4


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home
