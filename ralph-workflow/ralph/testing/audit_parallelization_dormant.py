"""Audit that planning keeps delegation optional and non-prescriptive."""

from __future__ import annotations

import sys
from pathlib import Path

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _read(rel_path: str) -> str:
    return (_PACKAGE_ROOT / rel_path).read_text(encoding="utf-8")


class Invariant:
    """One literal-string check the audit enforces."""

    def __init__(
        self, *, rel_path: str, present: tuple[str, ...] = (), absent: tuple[str, ...] = ()
    ) -> None:
        self.rel_path = rel_path
        self.present = present
        self.absent = absent

    def violations(self) -> list[str]:
        content = _read(self.rel_path)
        return [
            *[
                f"{self.rel_path}: missing required literal {needle!r}"
                for needle in self.present
                if needle not in content
            ],
            *[
                f"{self.rel_path}: forbidden literal still present {needle!r}"
                for needle in self.absent
                if needle in content
            ],
        ]


_INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        rel_path="prompts/templates/planning.jinja",
        present=("Use subagents only when independent repository discovery",),
        absent=("## Same-Workspace Parallel Worker Rules",),
    ),
    Invariant(
        rel_path="prompts/templates/planning_analysis.jinja",
        present=("do not grade document shape",),
        absent=("nine-dimension",),
    ),
)


def main(argv: list[str] | None = None) -> int:
    """Run the optional-delegation audit and return its process exit code."""
    del argv
    problems = [problem for invariant in _INVARIANTS for problem in invariant.violations()]
    if problems:
        print(f"PLANNING-GUIDANCE AUDIT FAILED: {len(problems)} invariant violation(s)")
        print("=" * 72)
        for problem in problems:
            print(f"  {problem}")
        return 1
    print("All planning-guidance invariants OK: delegation is optional and review is substantive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
