"""Behavioral prompt contracts for inaccurate and differently sized plans."""

from pathlib import Path

import pytest

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, SessionDrain, capability_template_variables
from ralph.workspace.memory import MemoryWorkspace


def _render_developer(tmp_path: Path, template_name: str) -> str:
    return prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Preserve the requested behavior.",
            plan_content="### [S-1] Replace a module that may not exist",
        ),
        workspace=MemoryWorkspace(root=str(tmp_path)),
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name=template_name,
    )


@pytest.mark.parametrize(
    "template_name",
    ("developer_iteration.jinja", "developer_iteration_continuation.jinja"),
)
def test_developer_prompts_reconcile_plan_items_without_weakening_request(
    tmp_path: Path, template_name: str
) -> None:
    prompt = _render_developer(tmp_path, template_name)

    assert "request and its acceptance criteria as authoritative" in prompt
    assert "start from the execution plan and follow its steps" in prompt
    assert "planning phase already performed exploration" in prompt
    assert "Do not repeat broad repository exploration" in prompt
    assert "first ready reference" in prompt
    assert "advance to the next ready reference" in prompt
    assert "Keep orientation and premise checks bounded" in prompt
    assert "do not re-read material already established by the plan" in prompt
    assert "`completed`" in prompt
    assert "`adapted`" in prompt
    assert "`not_applicable`" in prompt
    assert "Preserve every required plan reference" in prompt
    assert "Difficulty, elapsed time" in prompt
    assert "status: partial" in prompt


def test_continuation_preserves_verified_delivery_and_run_budget(tmp_path: Path) -> None:
    prompt = _render_developer(tmp_path, "developer_iteration_continuation.jinja")

    assert "## Verified delivery" in prompt
    assert "Keep each wait bounded" in prompt


def test_brokered_fallback_preserves_plan_loop_and_delivery_commitments() -> None:
    context = TemplateContext.default()
    session = SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT)
    base = {
        **capability_template_variables(session.capabilities, session.policy_flags),
        "PRODUCT_CRITERIA": "Preserve behavior.",
        "PRODUCT_CRITERIA_PATH": "PROMPT.md",
        "PLAN": "### [S-1] Implement the assigned change",
        "PLAN_PATH": ".agent/PLAN.md",
        "ANALYSIS_FEEDBACK": "",
        "ANALYSIS_FEEDBACK_PATH": "",
        "PRIOR_RESULT_STATUS": "",
        "LAST_RETRY_ERROR": "",
        "HAS_GIT_WRITE": "",
        "HAS_DOCS_MCP": "",
        "DOCS_MCP_PORT": "localhost:6280",
        "ARTIFACT_HISTORY_PATH": "",
        "SKILLS_INLINE_CONTENT": "",
        "SKILLS_MANIFEST_PATH": "",
        "VERIFICATION_COMMAND": "make verify",
        "RUN_BUDGET_SECONDS": "60",
        "EXEC_TOOL_NAME": "ralph_exec",
        "REPORT_PROGRESS_TOOL_NAME": "ralph_report_progress",
        "SUBMIT_MD_ARTIFACT_TOOL_REFERENCE": "ralph_submit_md_artifact",
        "DECLARE_COMPLETE_TOOL_REFERENCE": "declare_complete",
        "IS_CONTINUATION": "1",
    }

    for is_worker in (False, True):
        prompt = render_template(
            context.registry.get_template("developer_iteration_fallback"),
            {
                **base,
                "IS_WORKER": "1" if is_worker else "",
                "unit_id": "api",
                "description": "Implement API",
                "allowed_directories": "src/api",
                "WORKER_NAMESPACE": ".agent/workers/api",
                "WORKER_FALLBACK_PATH": ".agent/workers/api/tmp/development_result.md",
            },
            context.partials,
        )
        assert "## Plan fidelity" in prompt
        assert "## Verified delivery" in prompt
        assert "Keep each wait bounded" in prompt
        assert "when coordination costs less than sequential execution" in prompt
        assert "MUST use at least one sub-agent as a hard gate" not in prompt


def test_development_analyzer_separates_request_criteria_from_plan_routes() -> None:
    context = TemplateContext.default()
    session = SessionCapabilities.defaults_for_drain(SessionDrain.ANALYSIS)
    prompt = render_template(
        context.registry.get_template("development_analysis"),
        {
            **capability_template_variables(session.capabilities, session.policy_flags),
            "PRODUCT_CRITERIA_PATH": "fixtures/request.md",
            "PLAN_PATH": "fixtures/plan.md",
            "LAST_RETRY_ERROR": "",
            "HAS_DOCS_MCP": "",
            "DOCS_MCP_PORT": "localhost:6280",
        },
        context.partials,
    )

    assert "plan step is a proposed implementation route" in prompt
    assert "not an additional product requirement" in prompt
    assert "Independently determine" in prompt
    assert "adapted" in prompt
    assert "not applicable" in prompt
    assert "Do not reject solely because" in prompt
    assert "every required plan reference whose independently derived disposition" in prompt
    assert "independently derived plan dispositions" in prompt
    assert "even if the developer reported `partial` or `failed`" in prompt
    assert "only when localized unmet work is actionable" in prompt
    assert "`failed` closes this planning/development cycle" in prompt
