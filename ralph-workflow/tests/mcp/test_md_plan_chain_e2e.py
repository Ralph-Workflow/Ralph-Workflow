"""Public plan-validation and prompt contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.tools.md_artifact import handle_verify_md_artifact
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


def _verify_payload(plan: str) -> dict[str, object]:
    result = handle_verify_md_artifact(
        MockSession(), MockWorkspace(Path("/tmp")), {"artifact_type": "plan", "content": plan}
    )
    return json.loads(result.content[0].text)


def test_public_verify_accepts_executor_ready_plan() -> None:
    payload = _verify_payload(_plan())

    assert payload["valid"] is True
    assert payload["counts"] == {"error": 0, "info": 0, "warning": 0}


def test_public_verify_rejects_incomplete_plan() -> None:
    payload = _verify_payload(_plan().replace("Expect: the focused contract tests pass with exit code 0\n", ""))

    assert payload["valid"] is False
    assert any(item["rule_id"] == "PLAN020" for item in payload["diagnostics"])


def test_all_planning_variants_share_the_compact_contract() -> None:
    context = TemplateContext.default()
    for name in ("planning.jinja", "planning_fallback.jinja", "planning_edit.jinja", "planning_edit_fallback.jinja"):
        source = context.registry.get_template(name.removesuffix(".jinja"))
        assert "shared/_planning_thinking.j2" in source
        assert "shared/_planning_submission_mechanics.j2" in source

    mechanics = context.partials["shared/_planning_submission_mechanics"]
    assert "schema_version" in mechanics
    assert "stable `### [S-n] Title` steps" in mechanics


def test_planning_revision_variants_repair_every_finding_in_place() -> None:
    context = TemplateContext.default()
    for name in ("planning_edit.jinja", "planning_edit_fallback.jinja"):
        source = context.registry.get_template(name.removesuffix(".jinja"))
        assert "Repair every referenced `PA-###` finding" in source
        assert "target step or plan-level text" in source
