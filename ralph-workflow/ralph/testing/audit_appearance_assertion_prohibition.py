"""Fail-closed audit for UI plan items whose proof rests on an appearance assertion.

An appearance assertion (CSS/class/style/DOM) is forbidden as visual
design proof per criterion 10. This audit walks the development
result artifacts' plan-items-proven list and flags any UI plan item
whose proof cites a CSS property, class name, inline or computed
style, or DOM shape. The audit's response is actionable: it names
the artifact, the plan item, and the capture path the author should
use instead.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

APPEARANCE_ASSERTION_PROHIBITION = (
    "An appearance assertion (CSS/class/style/DOM) is NOT evidence of design quality. "
    "Design proof requires captures graded visually via the criterion 8 verdict."
)
APPEARANCE_TERMS = re.compile(r"\b(?:css|class|style|dom)\b", re.IGNORECASE)
UI_TERMS = re.compile(r"\b(?:ui|visual|design|appearance|layout|ux|component|screen|page)\b", re.IGNORECASE)
PROOF_TERMS = re.compile(r"\b(?:proof|proves|evidence|assert(?:ion|s)?|verify|verification)\b", re.IGNORECASE)
LOOK_TERMS = re.compile(r"\b(?:look(?:s|ing)?|appearance|visual quality|design quality|pixel|spacing|hierarchy|balance)\b", re.IGNORECASE)
CAPTURE_REMEDY = "Provide a capture path and a criterion 8 visual verdict (for example ralph://media/{artifact_id}) instead."


@dataclass(frozen=True)
class AppearanceAssertionViolation:
    """A single violation the audit flags: a test source line whose prose
    asserts how something looks via CSS/class/style/DOM.
    """

    path: str
    line: int
    text: str

    @property
    def message(self) -> str:
        """Human-readable one-line description of the violation."""
        return f"{self.path}:{self.line}: appearance assertions cannot prove UI design quality. {CAPTURE_REMEDY}"


def _source_is_executable_test(text: str) -> bool:
    """Return True if the source contains an Assert or Call (a real test)."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return False
    return any(isinstance(node, (ast.Assert, ast.Call)) for node in ast.walk(tree))


def find_appearance_assertions(text: str, path: str = "<memory>") -> list[AppearanceAssertionViolation]:
    """Find every violation in ``text``. Returns the canonical violation list."""
    lines = text.splitlines()
    anchors: list[int] = []
    for index, _line in enumerate(lines):
        window = " ".join(lines[max(0, index - 1) : min(len(lines), index + 2)])
        if not APPEARANCE_TERMS.search(window):
            continue
        if not UI_TERMS.search(window):
            continue
        if not PROOF_TERMS.search(window):
            continue
        if not LOOK_TERMS.search(window):
            continue
        anchors.append(index)
    deduped: list[int] = []
    for index in anchors:
        if deduped and index - deduped[-1] <= 2:
            continue
        deduped.append(index)
    return [
        AppearanceAssertionViolation(path, index + 1, lines[index].strip())
        for index in deduped
    ]


def audit_test_source(text: str, path: str = "<memory>") -> list[AppearanceAssertionViolation]:
    """Audit a single test source string. Returns violations."""
    if not _source_is_executable_test(text):
        return []
    return find_appearance_assertions(text, path)


def audit_test_files(root: str | Path) -> list[AppearanceAssertionViolation]:
    """Audit every tests/test_*.py under ``root``. Returns all violations."""
    base = Path(root)
    violations: list[AppearanceAssertionViolation] = []
    for path in sorted(base.glob("tests/test_*.py")):
        if path.name == "test_appearance_assertion_prohibition.py":
            continue
        violations.extend(audit_test_source(path.read_text(encoding="utf-8"), str(path)))
    return violations


def format_violations(violations: Iterable[AppearanceAssertionViolation]) -> str:
    """Format a list of violations as a one-line-per-violation string."""
    return "\n".join(violation.message for violation in violations)


def main() -> int:
    """CLI entry point: audit tests/, print violations, return non-zero on failure."""
    root = Path(__file__).resolve().parents[2]
    violations = audit_test_files(root)
    if violations:
        print("Appearance assertion audit failed:")
        print(format_violations(violations))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
