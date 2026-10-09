"""Planning prompts teach substantive, optional parallel conventions."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables


def _render_planner(name: str) -> str:
    context = TemplateContext.default()
    session = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)
    return render_template(
        context.registry.get_template(name),
        {
            **capability_template_variables(session.capabilities, session.policy_flags),
            "PRODUCT_CRITERIA": "Update independent command and documentation behavior.",
            "PRODUCT_CRITERIA_PATH": "fixtures/request.md",
            "ANALYSIS_FEEDBACK": "Avoidable serialization: split command and documentation units.",
            "ANALYSIS_FEEDBACK_PATH": "fixtures/feedback.md",
            "HAS_DOCS_MCP": "",
            "DOCS_MCP_PORT": "localhost:6280",
            "LAST_RETRY_ERROR": "",
            "SKILLS_INLINE_CONTENT": "",
            "DEVELOPMENT_BUDGET_MINUTES": "90",
            "DEVELOPMENT_MAX_PARALLEL_WORKERS": "8",
        },
        context.partials,
    )


@pytest.mark.parametrize(
    "name", ("planning", "planning_fallback", "planning_edit", "planning_edit_fallback")
)
def test_rendered_planners_recommend_parallel_work_without_format_rules(name: str) -> None:
    rendered = _render_planner(name)
    normalized = " ".join(rendered.split())

    for required in (
        "parallel work is the default",
        "two or more independent",
        "real coupling",
        "recommended",
        "Directories:",
        "Paths:",
        "Files:",
        "Depends on:",
        "shared contracts",
        "integration",
        "fan-in",
        "Subagents and parallel agents are always available",
        "## PARALLEL EXECUTION PLAN — DISPATCH MANIFEST",
        "PARALLEL:",
        "AFTER:",
        "Initial wave:",
        "Why not parallel:",
        "Do not make the executor infer",
        "90 minutes",
        "8 concurrent workers",
        "fit inside the development-phase budget",
        "critical path",
        "divided and parallelized enough",
        "requested outcomes, constraints, non-goals",
        "current call or data flow",
        "chosen approach",
        "consequential tradeoffs",
        "acceptance observation",
        "requirement-to-work-to-proof",
        "compatibility, migration, rollback, failure handling, security, and performance",
        "only when the request or repository evidence implicates them",
        "replanning trigger",
    ):
        assert required in normalized
    for forbidden in (
        "submission boundary checks",
        "size limit",
        "at least ten words",
        "max_work_units",
        "Validation Overrides",
        "schema_version",
        "HAS_SUBAGENTS",
        "required fields",
        "repair validation",
        "## Skills MCP",
    ):
        assert forbidden not in normalized
    if "edit" in name:
        assert "avoidable serialization" in normalized
        assert "proposed unit split" in normalized


def test_plan_format_doc_teaches_optional_ownership() -> None:
    from ralph.mcp.artifacts.format_docs import load_bundled_format_doc

    source = load_bundled_format_doc("plan")
    assert source is not None
    for text in ("## Work Units", "Paths:", "Directories:", "shared contracts", "integration"):
        assert text in source
