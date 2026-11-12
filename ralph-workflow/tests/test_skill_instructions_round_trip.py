from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.artifacts.format_docs import load_bundled_example
from ralph.mcp.tools.md_artifact import handle_verify_md_artifact
from ralph.workspace.memory import MemoryWorkspace
from tests._tool_artifact_2_helper_mocksession import MockSession

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PACKAGE_ROOT / "ralph" / "skills" / "content" / "submit-plan-artifact.md"


def test_installed_planning_skills_match_packaged_content() -> None:
    from ralph.skills import get_skill_content

    for name in ("submit-plan-artifact",):
        packaged = PACKAGE_ROOT / "ralph" / "skills" / "content" / f"{name}.md"
        assert get_skill_content(name) == packaged.read_text(encoding="utf-8")


def test_plan_skill_example_validates_with_zero_errors() -> None:
    example = load_bundled_example("plan")
    assert example is not None

    result = handle_verify_md_artifact(
        MockSession("planning"),
        MemoryWorkspace(),
        {"artifact_type": "plan", "content": example},
    )

    assert not result.is_error
    assert json.loads(result.content[0].text)["diagnostics"] == []


def test_plan_skill_documents_the_complete_markdown_workflow() -> None:
    body = SKILL_PATH.read_text(encoding="utf-8")

    for tool in (
        "ralph_submit_md_artifact",
        "ralph_edit_md_artifact",
        "ralph_discard_md_draft",
    ):
        assert tool in body
    assert "Parallel work is the default" in body
    assert "not enforce headings" in body
    assert "JSON" not in body
    assert "ralph_edit_md_plan_step" not in body
