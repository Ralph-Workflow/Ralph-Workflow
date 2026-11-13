from __future__ import annotations

from pathlib import Path

_POLICY_DIR = Path(__file__).resolve().parents[3] / "docs" / "ralph-workflow-policy"
_STARTER_DIR = Path(__file__).resolve().parents[2] / "ralph" / "project_policy" / "starters"

_POLICY_FILES = ("testing-policy.md", "verification-policy.md", "agent-policy.md")

_SHIPPED_SKILL_TECHNIQUE = (
    "WRITE the test before the production change",
    "watch it fail for the expected reason",
    "report the observed red result",
    "the purpose of the WRITE instruction",
    "I think it works",
    "report the actual command and outcome",
    "Report the actual command output",
)


def test_policy_copies_do_not_restate_the_shipped_skill_technique() -> None:
    violations: list[str] = []

    for name in _POLICY_FILES:
        for directory in (_POLICY_DIR, _STARTER_DIR):
            path = directory / name
            content = path.read_text(encoding="utf-8")
            for phrase in _SHIPPED_SKILL_TECHNIQUE:
                if phrase in content:
                    violations.append(f"{directory.name}/{name}: {phrase!r}")

    assert not violations, (
        "the shipped test-driven-development and verification-before-completion "
        "skills hold this technique; the policy keeps the rule: "
        f"{violations}"
    )
