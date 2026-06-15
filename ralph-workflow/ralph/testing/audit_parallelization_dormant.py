"""Audit that Ralph-managed fan-out is dormant and the agent-driven model is wired.

Enforces eight non-vacuous invariants across the planning prompt, the
continuation template, the bundled plan format doc, the effect-router
WARNING, the bundled pipeline.toml, the planning_analysis.jinja
rubric, the user-facing configuration docs, and the advanced
pipeline-configuration doc. Every check uses a real, current-state
phrase so the audit cannot be vacuously satisfied by strings that
never appear in the codebase.

Checks (the literals are the verified-real current strings):
  1. ``planning.jinja`` MUST contain ``## Agent-Driven Parallel Execution``
  2. ``planning.jinja`` MUST NOT contain ``## Same-Workspace Parallel Worker Rules``
  3. ``plan.md`` MUST contain ``agent-managed sub-agents`` AND ``fan-out is dormant``
  4. ``effect_router.py`` MUST contain ``Ralph-managed fan-out is dormant in this build``
  5. ``pipeline.toml`` MUST contain ``dispatch_mode = agent_subagents``
  6. ``planning_analysis.jinja`` MUST contain ``### 9. PARALLEL EXECUTION (AGENT-DRIVEN)``
  7. ``developer_iteration_continuation.jinja`` MUST contain the new
     ``## PARALLEL EXECUTION (when the plan declares`` heading AND MUST
     NOT contain the legacy ``fan-out`` (Ralph-managed) wording, so
     continuation runs cannot regress to Ralph-managed fan-out.
  8. ``configuration.md`` MUST contain ``subagent_capability`` (the
     ``[agents.*]`` default-resolution doc-pinned to prevent silent
     removal of the new H3 subsection that documents the bundled
     Claude sub-agent default)
  9. ``advanced-pipeline-configuration.md`` MUST contain ``dispatch_mode``
     (the ``[phases.<name>.parallelization]`` H3 already covers it; this
     invariant pins the existing surface so it cannot drift away from
     the bundled default)

The existing ``### 7. PARALLELIZATION SAFETY - MEDIUM`` heading in
``planning_analysis.jinja`` is part of the existing rubric and is NOT
flagged here.

Usage:
    python -m ralph.testing.audit_parallelization_dormant

Exit 0 = clean, 1 = at least one invariant violated.
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
        self,
        *,
        rel_path: str,
        present: tuple[str, ...] = (),
        absent: tuple[str, ...] = (),
    ) -> None:
        self.rel_path = rel_path
        self.present = present
        self.absent = absent

    def violations(self) -> list[str]:
        content = _read(self.rel_path)
        missing = [
            f"{self.rel_path}: missing required literal {needle!r}"
            for needle in self.present
            if needle not in content
        ]
        forbidden = [
            f"{self.rel_path}: forbidden literal still present {needle!r}"
            for needle in self.absent
            if needle in content
        ]
        return [*missing, *forbidden]


_INVARIANTS: tuple[Invariant, ...] = (
    Invariant(
        rel_path="prompts/templates/planning.jinja",
        present=("## Agent-Driven Parallel Execution",),
        absent=("## Same-Workspace Parallel Worker Rules",),
    ),
    Invariant(
        rel_path="mcp/artifacts/format_docs/plan.md",
        present=("agent-managed sub-agents", "fan-out is dormant"),
    ),
    Invariant(
        rel_path="pipeline/effect_router.py",
        present=("Ralph-managed fan-out is dormant in this build",),
    ),
    Invariant(
        rel_path="policy/defaults/pipeline.toml",
        present=('dispatch_mode = "agent_subagents"',),
    ),
    Invariant(
        rel_path="prompts/templates/planning_analysis.jinja",
        present=("### 9. PARALLEL EXECUTION (AGENT-DRIVEN)",),
    ),
    Invariant(
        rel_path="prompts/templates/developer_iteration_continuation.jinja",
        present=("## PARALLEL EXECUTION (when the plan declares",),
        absent=("fan-out",),
    ),
    Invariant(
        rel_path="../docs/sphinx/configuration.md",
        present=("subagent_capability",),
    ),
    Invariant(
        rel_path="../docs/sphinx/advanced-pipeline-configuration.md",
        present=("dispatch_mode",),
    ),
)


def main(argv: list[str] | None = None) -> int:
    """Run the parallelization-dormant audit and return the process exit code.

    Iterates over the literal-string ``Invariant`` objects in ``_INVARIANTS``,
    aggregates all violations across the planning prompt, the bundled plan
    format doc, the effect-router WARNING, the bundled ``pipeline.toml``,
    and the ``planning_analysis.jinja`` rubric. Prints a one-line summary
    on success or a labeled, line-broken failure banner on violation. Has
    no side effects beyond stdout output and ``sys.exit`` semantics.

    Args:
        argv: Unused positional argument list (kept for CLI symmetry with
            other audit entry points). Values are ignored.

    Returns:
        ``0`` when every invariant passes, ``1`` when at least one
        literal-string check fails.
    """
    del argv
    problems: list[str] = []
    for invariant in _INVARIANTS:
        problems.extend(invariant.violations())

    if problems:
        print(f"PARALLELIZATION-DORMANT AUDIT FAILED: {len(problems)} invariant violation(s)")
        print("=" * 72)
        for line in problems:
            print(f"  {line}")
        print()
        print(
            "The bundled default must keep parallel execution delegated to the AI agent's "
            "sub-agents, with Ralph-managed fan-out dormant. Re-read the rework plan in "
            "PLAN.md and restore the missing/forbidden literals."
        )
        return 1

    print(
        "All 8 invariants OK (parallelization-dormant audit): "
        "planning.jinja new heading present + old heading absent, "
        "plan.md agent-managed sub-agents + fan-out is dormant, "
        "effect_router.py WARNING, "
        "pipeline.toml dispatch_mode, "
        "planning_analysis.jinja ninth rubric dimension, "
        "continuation.jinja new heading present + fan-out absent, "
        "configuration.md subagent_capability doc-pinned, "
        "advanced-pipeline-configuration.md dispatch_mode doc-pinned."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
