"""Tests: analysis template payload contracts are correctly enforced."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.template_variables import (
    capability_template_variables,
    default_caps_and_flags_for_drain,
)
from ralph.recovery.retry_prompt import build_validation_retry_footer

_TEMPLATES_DIR = Path(__file__).parent.parent / "ralph" / "prompts" / "templates"

_RETRY_HINT_TEMPLATES = (
    "planning.jinja",
    "planning_edit.jinja",
    "commit_cleanup.jinja",
    "planning_analysis.jinja",
    "development_analysis.jinja",
    "developer_iteration_continuation.jinja",
    "developer_iteration_fallback.jinja",
    "policy_remediation.jinja",
    "policy_remediation_analysis.jinja",
)
_TEMPLATE_DRAINS = {
    "planning.jinja": SessionDrain.PLANNING,
    "planning_edit.jinja": SessionDrain.PLANNING,
    "commit_cleanup.jinja": SessionDrain.COMMIT,
    "planning_analysis.jinja": SessionDrain.ANALYSIS,
    "development_analysis.jinja": SessionDrain.DEVELOPMENT_ANALYSIS,
    "developer_iteration_continuation.jinja": SessionDrain.DEVELOPMENT,
    "developer_iteration_fallback.jinja": SessionDrain.DEVELOPMENT,
    "policy_remediation.jinja": SessionDrain.DEVELOPMENT,
    "policy_remediation_analysis.jinja": SessionDrain.ANALYSIS,
}


def _load(name: str) -> str:
    return (_TEMPLATES_DIR / name).read_text(encoding="utf-8")


def _render(name: str, last_retry_error: str) -> str:
    context = TemplateContext.default()
    capabilities, policy_flags = default_caps_and_flags_for_drain(_TEMPLATE_DRAINS[name])
    variables = {
        **capability_template_variables(capabilities, policy_flags),
        "LAST_RETRY_ERROR": last_retry_error,
        "PRODUCT_CRITERIA": "Repair the validation failure.",
        "PRODUCT_CRITERIA_PATH": "PROMPT.md",
        "PLAN": "### [S-1] Repair\n- Verify: pytest -q\n- Expect: passes",
        "PLAN_PATH": ".agent/PLAN.md",
        "ANALYSIS_FEEDBACK": "",
        "ANALYSIS_FEEDBACK_PATH": "",
        "ANALYSIS_FEEDBACK_STATUS": "",
        "PRIOR_RESULT_STATUS": "",
        "PRIOR_RESULT_SUMMARY": "",
        "PRIOR_RESULT_NEXT_STEPS": "",
        "PRIOR_RESULT_CONTINUATION": "",
        "IS_WORKER": "",
        "IS_CONTINUATION": "",
        "WORKER_NAMESPACE": "",
        "WORKER_FALLBACK_PATH": "",
        "ARTIFACT_HISTORY_PATH": "",
        "ARTIFACT_HISTORY_DIR": "",
        "SKILLS_INLINE_CONTENT": "",
        "HAS_DOCS_MCP": "",
        "DOCS_MCP_PORT": "localhost:6280",
        "DOCS_LOOKUP_PHASE": "",
        "DOCS_LOOKUP_VARIANT": "",
        "DOCS_LOOKUP_INTENT": "",
        "DOCS_LOOKUP_ACTION": "",
        "HIDE_ARTIFACT_SUBMISSION_GUIDANCE": "",
        "DIFF": "",
        "DIFF_PATH": "",
        "DELETE_DECISION_RULES": "No action.",
        "canonical_dir": "docs/ralph-workflow-policy",
        "findings_block": "No findings.",
        "analysis_feedback_block": "",
        "analysis_feedback_summary": "",
        "analysis_feedback_status": "",
        "gate_script_policy_path": "docs/gates.md",
        "migrated_marker": "RALPH-WORKFLOW-POLICY",
        "agents_block_begin": "AGENTS BEGIN",
        "agents_block_end": "AGENTS END",
        "approved_tools": "python",
        "submit_tool_names": "ralph_submit_md_artifact",
        "verify_tool_names": "ralph_verify_md_artifact",
        "declare_complete_tool_names": "declare_complete",
        "artifact_type": "policy_remediation_analysis_decision",
    }
    return render_template(context.registry.get_template(name), variables, context.partials)


class TestRetryHintGuardInTemplates:
    @pytest.mark.parametrize("name", _RETRY_HINT_TEMPLATES)
    def test_template_includes_the_shared_validation_failure_section(self, name: str) -> None:
        source = _load(name)

        assert source.startswith("{% include 'shared/_validation_failure.j2' -%}") or source.startswith(
            "{% if LAST_RETRY_ERROR %}{% include 'shared/_validation_failure.j2' %}{% endif -%}"
        ), f"{name}: must render the shared validation failure section first"

    @pytest.mark.parametrize("name", _RETRY_HINT_TEMPLATES)
    def test_active_validation_retry_starts_with_the_canonical_banner(self, name: str) -> None:
        retry_error = (
            "VALIDATION FAILURE\nSPEC001: missing required field\n"
            f"{build_validation_retry_footer()}"
        )
        rendered = _render(name, retry_error)

        assert rendered.startswith(retry_error)
        assert rendered.count(build_validation_retry_footer()) == 1

    @pytest.mark.parametrize("name", _RETRY_HINT_TEMPLATES)
    def test_inactive_retry_has_no_validation_banner_or_footer(self, name: str) -> None:
        rendered = _render(name, "")

        assert "VALIDATION FAILURE" not in rendered
        assert build_validation_retry_footer() not in rendered
