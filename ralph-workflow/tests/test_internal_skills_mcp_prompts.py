"""Prompt-quality contracts shared by packaged markdown-artifact skills."""

from __future__ import annotations

import re
from pathlib import Path

from ralph.mcp.artifacts.markdown._spec import parse_and_validate
from ralph.mcp.artifacts.markdown.specs.plan import PLAN_SPEC
from ralph.mcp.tools.names import RalphToolName

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / "ralph" / "skills" / "content"
TEMPLATE_DIR = ROOT / "ralph" / "prompts" / "templates"
ARTIFACT_SKILLS = (
    "submit-artifact.md",
    "submit-plan-artifact.md",
    "submit-plan-step-edits.md",
    "submit-commit-message-artifact.md",
    "submit-development-result-artifact.md",
    "submit-commit-cleanup-artifact.md",
)


def _read(name: str) -> str:
    return (SKILL_DIR / name).read_text(encoding="utf-8")


def test_packaged_artifact_skills_are_trigger_oriented_markdown_guides() -> None:
    for name in ARTIFACT_SKILLS:
        text = _read(name)
        frontmatter = re.match(r"---\n(.*?)\n---", text, re.DOTALL)
        assert frontmatter is not None
        assert "description: Use when" in frontmatter.group(1)
        assert "version: 2.0.0" in frontmatter.group(1)
        assert "ralph_submit_md_artifact" in text or name == "submit-plan-step-edits.md"
        assert "ralph_submit_artifact" not in text


def test_packaged_artifact_skills_reference_only_registered_ralph_tools() -> None:
    known = {tool.value for tool in RalphToolName}
    unknown: dict[str, list[str]] = {}
    for name in ARTIFACT_SKILLS:
        references = set(re.findall(r"\bralph_[a-z0-9_]+", _read(name)))
        missing = sorted(references - known)
        if missing:
            unknown[name] = missing

    assert unknown == {}


def test_plan_skill_native_markdown_example_matches_validator() -> None:
    text = _read("submit-plan-artifact.md")
    match = re.search(r"Worked example:\s*```markdown\n(.*?)\n```", text, re.DOTALL)
    assert match is not None

    normalized, diagnostics = parse_and_validate(match.group(1), PLAN_SPEC)

    assert not [item for item in diagnostics if item.severity == "error"]
    assert len(normalized["steps"]) >= 2


def test_plan_edit_skill_teaches_stable_id_targeted_repair() -> None:
    text = _read("submit-plan-step-edits.md")

    assert "ralph_edit_md_plan_step" in text
    assert "replacement" in text
    assert "### [S-" in text
    assert "never renumber" in text.lower()
    assert "JSON" not in text


def test_prompt_templates_use_markdown_tools_without_retired_json_vocabulary() -> None:
    planning_templates = tuple(TEMPLATE_DIR.glob("planning*.jinja"))
    combined = "\n".join(path.read_text(encoding="utf-8") for path in planning_templates)

    for variable in (
        "SUBMIT_MD_ARTIFACT_TOOL_REFERENCE",
        "VERIFY_MD_ARTIFACT_TOOL_REFERENCE",
        "EDIT_MD_PLAN_STEP_TOOL_REFERENCE",
    ):
        assert variable in combined
    for retired in (
        "ralph_submit_plan_section",
        "ralph_submit_plan_sections",
        "ralph_validate_draft",
        "ralph_finalize_plan",
        "plan.json",
    ):
        assert retired not in combined
