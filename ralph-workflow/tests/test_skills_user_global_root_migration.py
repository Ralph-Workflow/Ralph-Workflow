from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ralph.skills._agent_paths import agent_skill_roots
from ralph.skills.manager import install_baseline_skills

pytestmark = pytest.mark.timeout_seconds(10)

RETIRED_MANAGED_NAME = "retired-managed-skill"
EDITED_MANAGED_NAME = "edited-managed-skill"
USER_OWNED_NAME = "user-owned-skill"

MANAGED_MARKER_NAME = ".ralph-managed.json"


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _plant_managed_skill(root: Path, name: str, content: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    (skill_dir / MANAGED_MARKER_NAME).write_text(
        json.dumps(
            {
                "managed_by": "ralph-workflow",
                "skill": name,
                "installed_content_sha256": _digest(content),
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return skill_dir


def _plant_user_owned_skill(root: Path, name: str, content: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
    return skill_dir


def test_the_migration_prunes_only_managed_skills_at_every_user_global_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))

    roots = tuple(root.resolve() for root in agent_skill_roots())

    for root in roots:
        _plant_managed_skill(root, RETIRED_MANAGED_NAME, "# retired\n")
        edited = _plant_managed_skill(root, EDITED_MANAGED_NAME, "# installed\n")
        (edited / "SKILL.md").write_text("# my edit\n", encoding="utf-8")
        _plant_user_owned_skill(root, USER_OWNED_NAME, "# mine\n")

    _entry, failures = install_baseline_skills()

    for root in roots:
        assert not (root / RETIRED_MANAGED_NAME).exists(), (
            f"a retired Ralph-managed skill must be pruned at {root}"
        )
        assert (root / EDITED_MANAGED_NAME / "SKILL.md").read_text(encoding="utf-8") == (
            "# my edit\n"
        ), f"a user-edited managed skill must survive at {root}"
        assert (root / USER_OWNED_NAME / "SKILL.md").read_text(encoding="utf-8") == ("# mine\n"), (
            f"a user-owned skill must survive at {root}"
        )

    assert f"skills-prune-preserved-{EDITED_MANAGED_NAME}" in failures, failures
