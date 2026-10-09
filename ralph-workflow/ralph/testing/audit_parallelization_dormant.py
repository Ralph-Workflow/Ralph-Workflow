"""Audit that planning treats subagent delegation and parallel work units as
the default rather than a reluctant exception.

The audit pins the literals that mark the unconditional posture: a planning
prompt that names read-only subagent delegation for exploration, research, and
verification, plus the shared delegation guidance that names context
isolation. Forbidden literals are the relics of the previous
``HAS_SUBAGENTS``-gated wording (e.g. ``ralph coordinate``, parallel-worker
rules) that would re-introduce capability detection or a coordinated-worker
abstraction Ralph does not implement.
"""

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
        present=("Delegate exploration, research, and verification to read-only subagents",),
        absent=("## Same-Workspace Parallel Worker Rules",),
    ),
    Invariant(
        rel_path="prompts/templates/shared/_subagents.j2",
        present=(
            "DELEGATION GUIDANCE",
            "shorten elapsed time, preserve accuracy through independent",
            "context isolation",
            "Two independent tasks are enough to fan out",
        ),
        absent=(
            "secondary benefits, not the goal",
            "ralph coordinate",
            "Dispatch every work unit to a sub-agent",
            "MUST run as a sub-agent",
        ),
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
    print(
        "All planning-guidance invariants OK: delegation and parallel work units are first-class."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
