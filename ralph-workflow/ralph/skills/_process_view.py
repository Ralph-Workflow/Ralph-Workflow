"""Process-scoped skill filesystem materialization."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal, cast

from ralph.skills._content import (
    _MANAGED_MARKER,
    BASELINE_SKILL_NAMES,
    get_skill_content,
    materialize_skills_to_dir,
)

_ENV_VAR = "RALPH_SKILLS_PROCESS_DIR"


def _personal_skills_dir() -> Path:
    return Path.home() / ".claude" / "skills"


def _project_skills_dir() -> Path:
    return Path.cwd() / ".claude" / "skills"


def _merge_external_skills(target: Path) -> None:
    for skills_dir in (_personal_skills_dir(), _project_skills_dir()):
        if not skills_dir.is_dir():
            continue
        for skill_dir in sorted(p for p in skills_dir.iterdir() if p.is_dir()):
            source = skill_dir / "SKILL.md"
            if not source.is_file():
                continue
            (target / f"{skill_dir.name}.md").write_text(
                source.read_text(encoding="utf-8"),
                encoding="utf-8",
            )


def _machine_global_skills_dir() -> Path:
    return Path.home() / ".claude" / "skills"


def _is_valid_managed_skill(skills_dir: Path, name: str) -> bool:
    skill_file = skills_dir / name / "SKILL.md"
    marker_file = skills_dir / name / _MANAGED_MARKER
    if not (skill_file.is_file() and marker_file.is_file()):
        return False
    try:
        raw_marker = cast("object", json.loads(marker_file.read_text(encoding="utf-8")))
    except Exception:
        return False
    if not isinstance(raw_marker, dict):
        return False
    marker = cast("dict[str, object]", raw_marker)
    if not (marker.get("managed_by") == "ralph-workflow" and marker.get("skill") == name):
        return False
    return skill_file.read_text(encoding="utf-8") == get_skill_content(name)


def has_machine_global_skills() -> bool:
    skills_dir = _machine_global_skills_dir()
    if not skills_dir.is_dir():
        return False
    return all(_is_valid_managed_skill(skills_dir, name) for name in BASELINE_SKILL_NAMES)


class SkillsProcessView(AbstractContextManager[Path]):
    """Materialize baseline skills into a process-scoped filesystem view."""

    def __init__(self, target_dir: Path | None = None) -> None:
        self._target_dir = target_dir
        self._tempdir: tempfile.TemporaryDirectory[str] | None = None

    def __enter__(self) -> Path:
        if self._target_dir is None:
            self._tempdir = tempfile.TemporaryDirectory(prefix="ralph-skills-")
            target = Path(self._tempdir.name)
        else:
            target = self._target_dir
            target.mkdir(parents=True, exist_ok=True)
        _merge_external_skills(target)
        materialize_skills_to_dir(target)
        os.environ[_ENV_VAR] = str(target)
        return target

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> Literal[False]:
        os.environ.pop(_ENV_VAR, None)
        if self._tempdir is not None:
            self._tempdir.cleanup()
        return False


__all__ = ["SkillsProcessView", "has_machine_global_skills"]
