"""Process-scoped skill filesystem materialization."""

from __future__ import annotations

import os
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Literal

from ralph.skills._content import BASELINE_SKILL_NAMES, materialize_skills_to_dir

_ENV_VAR = "RALPH_SKILLS_PROCESS_DIR"


def _machine_global_skills_dir() -> Path:
    return Path.home() / ".claude" / "plugins" / "ralph-workflow-skills" / "skills"


def has_machine_global_skills() -> bool:
    skills_dir = _machine_global_skills_dir()
    if not skills_dir.is_dir():
        return False
    present = {f.stem for f in skills_dir.glob("*.md")}
    return set(BASELINE_SKILL_NAMES).issubset(present)


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
