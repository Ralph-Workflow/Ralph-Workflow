"""Round-trip the plan skill's native-markdown worked example."""

from __future__ import annotations

import re
from pathlib import Path

from ralph.mcp.artifacts.markdown._spec import parse_and_validate
from ralph.mcp.artifacts.markdown.specs.plan import PLAN_SPEC

SKILL_PATH = (
    Path(__file__).resolve().parents[1]
    / "ralph"
    / "skills"
    / "content"
    / "submit-plan-artifact.md"
)


def _worked_plan() -> str:
    body = SKILL_PATH.read_text(encoding="utf-8")
    match = re.search(r"Worked example:\s*```markdown\n(.*?)\n```", body, re.DOTALL)
    assert match is not None, "plan skill must include a fenced markdown worked example"
    return match.group(1)


def test_plan_skill_example_validates_with_zero_errors() -> None:
    normalized, diagnostics = parse_and_validate(_worked_plan(), PLAN_SPEC)

    assert not [item for item in diagnostics if item.severity == "error"]
    assert normalized["steps"]
    assert normalized["verification_strategy"]


def test_plan_skill_documents_the_complete_markdown_workflow() -> None:
    body = SKILL_PATH.read_text(encoding="utf-8")

    for tool in (
        "ralph_verify_md_artifact",
        "ralph_submit_md_artifact",
        "ralph_stage_md_artifact",
        "ralph_get_md_draft",
        "ralph_finalize_md_artifact",
        "ralph_discard_md_draft",
        "ralph_edit_md_plan_step",
    ):
        assert tool in body
    assert "### [S-" in body
    assert "Depends on:" in body
    assert "JSON" not in body
