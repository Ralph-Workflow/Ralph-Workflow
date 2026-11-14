from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

CONFIG_ENV_MODULE = "ralph.config._env"
FIX_HINT = "restructure so the type checker can verify it"


@dataclass(frozen=True, slots=True)
class Finding:
    path: str
    line: int
    construct: str
    code: str
    rule_id: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.construct} ({self.rule_id}) — {FIX_HINT}"

    def as_json(self) -> dict[str, str | int]:
        return {
            "path": self.path,
            "line": self.line,
            "construct": self.construct,
            "code": self.code,
            "rule_id": self.rule_id,
            "fix_hint": FIX_HINT,
        }


class _AuditInputError(Exception):
    pass


def _label(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()
