"""Public plan-validation and prompt contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.tools.md_artifact import handle_submit_md_artifact, handle_verify_md_artifact
from ralph.prompts.template_context import TemplateContext
from tests._tool_artifact_2_helper_mocksession import MockSession
from tests._tool_artifact_2_helper_mockworkspace import MockWorkspace


def _plan() -> str:
    return """---
type: plan
---
## Work
### [S-1] Update validation
Change the validator and prove the focused contract.
Type: file_change
Files:
- modify ralph/mcp/artifacts/markdown/specs/plan.py
Verify: uv run pytest -q tests/mcp/test_md_plan_chain_e2e.py
Expect: the focused contract tests pass with exit code 0
"""


def _prose_plan() -> str:
    return """---
type: plan
---
This is a prose plan with no headings and more than ten words so it clears
the readability floor. The implementation will touch the spec, the
validation module, and the tool side. The development phase and the
analyzer read the raw text without requiring structure.
"""


def _decision(step: str) -> str:
    return f"""---
type: planning_analysis_decision
status: request_changes
---
## Summary
- [SUM-1] The plan needs correction.
## What Came Up Short
- [PA-001] Step: [{step}] Criterion: the step provides a runnable verification command. Expected observation: the command resolves in this repository. Proposed revision: name the runnable command in the step. Verdict: not met. Evidence: the command path is missing. Location: {step} Verify field. Cost: discovery work on every iteration.
## Criterion Verdicts
- [PA-001] Step: [{step}] Criterion: the step provides a runnable verification command. Expected observation: the command resolves in this repository. Proposed revision: name the runnable command in the step. Verdict: not met. Evidence: the command path is missing. Location: {step} Verify field. Cost: discovery work on every iteration.
"""


def _verify_payload(plan: str) -> dict[str, object]:
    result = handle_verify_md_artifact(
        MockSession(), MockWorkspace(Path("/tmp")), {"artifact_type": "plan", "content": plan}
    )
    return json.loads(result.content[0].text)


def test_public_verify_accepts_executor_ready_plan() -> None:
    payload = _verify_payload(_plan())

    assert payload["valid"] is True
    assert payload["counts"] == {"error": 0, "info": 0, "warning": 0}


def test_public_verify_accepts_prose_plan() -> None:
    """A prose plan with no headings is accepted with no error diagnostics."""
    payload = _verify_payload(_prose_plan())

    assert payload["valid"] is True
    assert payload["counts"] == {"error": 0, "info": 0, "warning": 0}


def test_public_verify_accepts_plan_with_duplicate_step_ids() -> None:
    """Duplicate step IDs are best-effort, not a structural error."""
    duplicate = (
        _plan()
        + """
### [S-1] Duplicate step
Type: file_change
Files:
- modify ralph/duplicate.py
Verify: uv run pytest -q tests/mcp/test_md_plan_chain_e2e.py
Expect: the focused contract tests pass with exit code 0
"""
    )
    payload = _verify_payload(duplicate)

    assert payload["valid"] is True
    assert payload["counts"]["error"] == 0


def test_submission_rejects_planning_finding_for_unknown_plan_step(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    assert not handle_submit_md_artifact(
        session, workspace, {"artifact_type": "plan", "content": _plan()}
    ).is_error

    result = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "planning_analysis_decision", "content": _decision("S-99")},
    )
    payload = json.loads(result.content[0].text)

    assert result.is_error is True
    assert any(
        item["rule_id"] == "ANALYSIS004" and item["line"] == 10 and "S-99" in item["message"]
        for item in payload["diagnostics"]
    )


def test_planning_prompts_share_the_compact_contract() -> None:
    """The four planning variants share their shared partials."""
    context = TemplateContext.default()
    for name in (
        "planning.jinja",
        "planning_fallback.jinja",
        "planning_edit.jinja",
        "planning_edit_fallback.jinja",
    ):
        source = context.registry.get_template(name.removesuffix(".jinja"))
        assert "shared/_planning_thinking.j2" in source
        assert "shared/_planning_submission_mechanics.j2" in source


def test_planning_edit_variants_apply_substantive_feedback() -> None:
    """Planning edits repair supported findings and split avoidable serialization."""
    context = TemplateContext.default()
    for name in ("planning_edit.jinja", "planning_edit_fallback.jinja"):
        source = context.registry.get_template(name.removesuffix(".jinja"))
        assert "repository evidence" in source
        assert "avoidable serialization" in source
        assert "integration" in source
