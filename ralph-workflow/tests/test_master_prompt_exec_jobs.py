"""Regression tests for exec job flow guidance in master prompt and shared MCP partial."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.config.enums import AgentTransport
from ralph.mcp.protocol.capability_mapping import Capability as RalphCapability
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts import template_variables
from ralph.prompts.master_prompt import (
    build_master_prompt,
    exec_job_guidance_text,
    materialize_master_prompt,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template

if TYPE_CHECKING:
    from pathlib import Path


def test_exec_job_guidance_text_contains_required_contract_elements() -> None:
    """The exec job guidance text must state all key operational contract elements."""
    text = exec_job_guidance_text()
    assert "soft reply budget" in text
    assert "job_id" in text
    assert "exec_wait" in text
    assert "exec_cancel" in text
    assert "timeout_ms" in text
    assert "stall window" in text
    assert "max_runtime_ms" in text
    assert "do not re-run" in text


@pytest.mark.parametrize("planning_style", [True, False])
def test_build_master_prompt_includes_exec_job_guidance(planning_style: bool) -> None:
    """Master prompt must render exec job guidance in both planning and execution styles."""
    prompt = build_master_prompt(
        planning_style=planning_style,
        master_prompt_path="/tmp/test_master_prompt.md",
        product_criteria_path="/tmp/PRODUCT_CRITERIA.md",
        current_plan_path="/tmp/PLAN.md" if not planning_style else None,
    )
    assert "EXEC JOB FLOW:" in prompt
    assert "soft reply budget" in prompt
    assert "job_id" in prompt
    assert "exec_wait" in prompt
    assert "exec_cancel" in prompt
    assert "timeout_ms" in prompt
    assert "stall window" in prompt
    assert "max_runtime_ms" in prompt
    assert "do not re-run" in prompt


@pytest.mark.parametrize("transport", [None, AgentTransport.AGY, AgentTransport.CLAUDE])
def test_build_master_prompt_preserves_exec_guidance_across_transports(
    transport: AgentTransport | None,
) -> None:
    """Exec job flow guidance must be present regardless of agent transport."""
    prompt = build_master_prompt(
        planning_style=False,
        master_prompt_path="/tmp/transport_master.md",
        product_criteria_path="/tmp/PRODUCT_CRITERIA.md",
        current_plan_path=None,
        transport=transport,
    )
    assert "EXEC JOB FLOW:" in prompt
    assert "exec_wait" in prompt
    assert "exec_cancel" in prompt
    assert "stall window" in prompt
    if transport is AgentTransport.AGY:
        assert "call_mcp_tool" in prompt


def test_materialize_master_prompt_writes_exec_job_guidance(tmp_path: Path) -> None:
    """Materializing the master prompt writes durable file with exec job guidance."""
    (tmp_path / "PROMPT.md").write_text("Test prompt task criteria", encoding="utf-8")
    master_path = materialize_master_prompt(
        workspace_root=tmp_path,
        name="development",
        planning_style=False,
    )
    content = tmp_path.joinpath(master_path).read_text(encoding="utf-8")
    assert "EXEC JOB FLOW:" in content
    assert "soft reply budget" in content
    assert "job_id" in content
    assert "exec_wait" in content
    assert "exec_cancel" in content
    assert "stall window" in content
    assert "max_runtime_ms" in content
    assert "do not re-run" in content


def test_shared_mcp_tools_partial_renders_exec_job_guidance() -> None:
    """The shared _mcp_tools partial must render exec job guidance when exec is granted."""
    ctx = TemplateContext.default()
    caps, flags = template_variables.default_caps_and_flags_for_drain(SessionDrain.DEVELOPMENT)
    variables = template_variables.capability_template_variables(caps, flags)
    partial = ctx.partials["_mcp_tools"]
    rendered = render_template(partial, variables, ctx.partials)

    assert "soft reply budget" in rendered
    assert "job_id" in rendered
    assert "`exec_wait`" in rendered
    assert "`exec_cancel`" in rendered
    assert "timeout_ms" in rendered
    assert "stall window" in rendered
    assert "max_runtime_ms" in rendered
    assert "do not re-run" in rendered


def test_shared_mcp_tools_partial_omits_exec_guidance_when_exec_not_granted() -> None:
    """When HAS_MCP_EXEC is false, the shared partial omits exec job guidance."""
    ctx = TemplateContext.default()
    caps, flags = template_variables.default_caps_and_flags_for_drain(SessionDrain.DEVELOPMENT)
    variables = template_variables.capability_template_variables(caps, flags)
    variables["HAS_MCP_EXEC"] = "false"
    variables["EXEC_WAIT_TOOL_REFERENCE"] = ""
    partial = ctx.partials["_mcp_tools"]
    rendered = render_template(partial, variables, ctx.partials)

    assert "soft reply budget" not in rendered
    assert "stall window" not in rendered
    assert "max_runtime_ms" not in rendered


@pytest.mark.parametrize(
    "drain",
    [
        SessionDrain.DEVELOPMENT,
        SessionDrain.PLANNING,
        SessionDrain.DEVELOPMENT_ANALYSIS,
        SessionDrain.ANALYSIS,
    ],
)
def test_shared_mcp_tools_partial_renders_exec_guidance_across_production_drains(
    drain: SessionDrain,
) -> None:
    """Every production drain granting process execution includes the exec guidance."""
    ctx = TemplateContext.default()
    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    assert caps.contains(RalphCapability.PROCESS_EXEC_BOUNDED)

    variables = template_variables.capability_template_variables(caps, flags)
    partial = ctx.partials["_mcp_tools"]
    rendered = render_template(partial, variables, ctx.partials)

    assert "soft reply budget" in rendered
    assert "job_id" in rendered
    assert "`exec_wait`" in rendered
    assert "`exec_cancel`" in rendered
    assert "timeout_ms" in rendered
    assert "stall window" in rendered
    assert "max_runtime_ms" in rendered
    assert "do not re-run" in rendered
