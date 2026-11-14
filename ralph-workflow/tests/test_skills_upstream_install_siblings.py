from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from ralph.skills import upstream
from tests.test_skills_upstream_install import (
    _NOW,
    _SKILL_FILES,
    _client_for,
    _install_fixture_release,
    _tree_snapshot,
)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return home


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
