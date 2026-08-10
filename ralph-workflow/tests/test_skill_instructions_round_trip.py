"""Round-trip the plan skill's native-markdown worked example."""

from __future__ import annotations

from pathlib import Path

from ralph.mcp.artifacts.format_docs import load_bundled_example
from ralph.mcp.artifacts.markdown._spec import parse_and_validate
from ralph.mcp.artifacts.markdown.specs.plan import PLAN_SPEC

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PACKAGE_ROOT / "ralph" / "skills" / "content" / "submit-plan-artifact.md"


def test_installed_planning_skills_match_packaged_content() -> None:
    for name in ("submit-plan-artifact", "writing-plans"):
        packaged = PACKAGE_ROOT / "ralph" / "skills" / "content" / f"{name}.md"
        installed = PACKAGE_ROOT.parent / ".opencode" / "skills" / name / "SKILL.md"
        assert installed.read_text(encoding="utf-8") == packaged.read_text(encoding="utf-8")


def test_plan_skill_example_validates_with_zero_errors() -> None:
    """submit-plan-artifact.md no longer embeds a worked example inline.

    It points readers at the bundled validator-backed example instead (see
    ``ralph/mcp/artifacts/format_docs/plan.md``'s "Example" section); prove
    that bundled example is itself real and valid.
    """
    example = load_bundled_example("plan")
    assert example is not None

    normalized, diagnostics = parse_and_validate(example, PLAN_SPEC)

    assert not [item for item in diagnostics if item.severity == "error"]
    assert normalized["steps"]


def test_plan_skill_documents_the_complete_markdown_workflow() -> None:
    """The condensed skill documents the core submit/edit loop it actually teaches.

    ``submit-plan-artifact.md`` was condensed to the mandatory executor-ready
    contract and no longer walks through the full staging toolkit
    (``ralph_stage_md_artifact`` / ``ralph_get_md_draft`` /
    ``ralph_finalize_md_artifact``) inline -- ``ralph_edit_md_artifact``
    covers the staged-repair path and auto-submits, and
    ``ralph_discard_md_draft`` covers the wholesale-restart path. This
    mirrors ``ralph/mcp/artifacts/format_docs/plan.md``'s own condensed
    "Submission" section.
    """
    body = SKILL_PATH.read_text(encoding="utf-8")

    for tool in (
        "ralph_submit_md_artifact",
        "ralph_edit_md_artifact",
        "ralph_discard_md_draft",
    ):
        assert tool in body
    assert "### [S-n] Title" in body
    assert "JSON" not in body
    assert "ralph_edit_md_plan_step" not in body
