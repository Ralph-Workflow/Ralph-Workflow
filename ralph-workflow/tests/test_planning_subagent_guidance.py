"""Rendered regression checks for concise planning guidance."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables


def _render_planner(name: str, *, budget: str = "90", worker_cap: str = "8") -> str:
    context = TemplateContext.default()
    session = SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING)
    return render_template(
        context.registry.get_template(name),
        {
            **capability_template_variables(session.capabilities, session.policy_flags),
            "PRODUCT_CRITERIA": "Update independent command and documentation behavior.",
            "PRODUCT_CRITERIA_PATH": "fixtures/request.md",
            "PLAN_PATH": "fixtures/plan.md",
            "ANALYSIS_FEEDBACK": "Avoidable serialization: split command and documentation units.",
            "ANALYSIS_FEEDBACK_PATH": "fixtures/feedback.md",
            "HAS_DOCS_MCP": "",
            "DOCS_MCP_PORT": "localhost:6280",
            "LAST_RETRY_ERROR": "",
            "SKILLS_INLINE_CONTENT": "",
            "DEVELOPMENT_BUDGET_MINUTES": budget,
            "DEVELOPMENT_MAX_PARALLEL_WORKERS": worker_cap,
        },
        context.partials,
    )


@pytest.mark.parametrize(
    "name", ("planning", "planning_fallback", "planning_edit", "planning_edit_fallback")
)
def test_rendered_planners_delegate_independent_work_by_default(name: str) -> None:
    rendered = " ".join(_render_planner(name).split())

    for required in (
        "Delegate exploration, research, verification, and review to read-only",
        "Fan-out is the default",
        "multiple independent areas",
        "Two independent tasks are enough to fan out",
    ):
        assert required in rendered
    assert "Use a subagent only" not in rendered


def test_planning_analysis_requires_an_executor_visible_dispatch_decision() -> None:
    rendered = " ".join(_render_planner("planning_analysis").split())

    assert "executor-visible dispatch decision" in rendered
    assert "initial ready set" in rendered
    assert "Why not parallel" in rendered
    assert "90 minutes" in rendered
    assert "8 concurrent workers" in rendered
    assert "fit inside the development-phase budget" in rendered
    assert "parallelized enough" in rendered
    assert "use subagents" in rendered
    assert "Do not return `request_changes` merely because a plan is" in rendered
    assert "lack confidence" in rendered
    assert "oversized total workload is acceptable" in rendered


def test_planning_analysis_handles_unknown_timebox_and_one_worker_capacity() -> None:
    rendered = " ".join(
        _render_planner("planning_analysis", budget="unknown", worker_cap="1").split()
    )

    assert "no numerical development timebox is configured" in rendered
    assert "not numerically evaluable" in rendered
    assert "at most 1 concurrent workers" in rendered
    assert "configured one-worker cap" in rendered
