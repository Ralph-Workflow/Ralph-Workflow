"""Round-trip the plan skill's native-markdown worked example."""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.artifacts.format_docs import load_bundled_example
from ralph.mcp.tools.md_artifact import handle_verify_md_artifact
from ralph.workspace.memory import MemoryWorkspace
from tests._tool_artifact_2_helper_mocksession import MockSession

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
SKILL_PATH = PACKAGE_ROOT / "ralph" / "skills" / "content" / "submit-plan-artifact.md"
# RALPH-TEST-REASON: the committed skill mirror trees leave git; the installer materializes them locally, so the round trip compares the packaged content instead


def test_installed_planning_skills_match_packaged_content() -> None:
    """The skill the installer writes must equal the packaged skill, byte for byte.

    The committed project mirror is no longer tracked in git (the installer
    re-materializes it locally on every run), so this pins the packaged
    content that the installer materializes through the PUBLIC accessor.
    """
    from ralph.skills import get_skill_content

    for name in ("submit-plan-artifact",):
        packaged = PACKAGE_ROOT / "ralph" / "skills" / "content" / f"{name}.md"
        assert get_skill_content(name) == packaged.read_text(encoding="utf-8")


def test_plan_skill_example_validates_with_zero_errors() -> None:
    """submit-plan-artifact.md no longer embeds a worked example inline.

    It points readers at the bundled validator-backed example instead (see
    ``ralph/mcp/artifacts/format_docs/plan.md``'s "Example" section); prove
    that bundled example is itself real and valid.
    """
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
    assert "Parallel work is the default" in body
    assert "not enforce headings" in body
    assert "JSON" not in body
    assert "ralph_edit_md_plan_step" not in body
