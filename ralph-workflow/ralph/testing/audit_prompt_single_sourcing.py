"""Detect divergent copies of the three deliberately cross-surface statements."""

from __future__ import annotations

from pathlib import Path

from ralph.prompts.template_registry import packaged_template_root

_POLICY_PATH = Path(__file__).parents[1] / "project_policy" / "starters" / "verification-policy.md"
_ALLOWED_STATEMENTS = (
    "A run is judged on **time to a correct, verified change**; speed never permits partial proof.",
    "Prevent an extra pass first; within a pass, the cost of proving the change is the largest term; where they conflict, avoid the extra pass.",
    "**fast path first**, **a slow gate is a defect**, **do not leave the loop slower**, and **checks answer consistently**",
)


def collect_violations() -> list[str]:
    """Return missing canonical cross-surface statements."""
    policy = _POLICY_PATH.read_text(encoding="utf-8")
    templates = (
        "\n".join(
            path.read_text(encoding="utf-8") for path in packaged_template_root().rglob("*.jinja")
        )
        + "\n"
        + "\n".join(
            path.read_text(encoding="utf-8") for path in packaged_template_root().rglob("*.j2")
        )
    )
    return [
        statement
        for statement in _ALLOWED_STATEMENTS
        if statement not in policy or statement not in templates
    ]


def main() -> int:
    """Print violations and return a conventional audit exit code."""
    violations = collect_violations()
    for statement in violations:
        print(f"prompt single-sourcing audit: missing canonical statement: {statement}")
    return int(bool(violations))


if __name__ == "__main__":
    raise SystemExit(main())
