from __future__ import annotations

import re

import pytest

from ralph.config.mcp_models import McpConfig
from ralph.mcp.artifacts.format_docs import load_bundled_format_doc
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.protocol.capability_mapping import Capability as RalphCapability
from ralph.mcp.tool_contract import visible_tool_names_for_capabilities
from ralph.mcp.tools.bridge._registry import tool_specs
from ralph.mcp.tools.names import RalphToolName
from ralph.prompts import template_variables
from ralph.prompts.template_context import TemplateContext
from ralph.skills import get_skill_content

PLANNING_SKILLS = ("submit-plan-artifact.md",)

ARTIFACT_SKILLS = (
    "submit-artifact.md",
    "submit-plan-artifact.md",
    "submit-commit-message-artifact.md",
    "submit-development-result-artifact.md",
    "submit-development-analysis-decision-artifact.md",
    "submit-commit-review-artifact.md",
)


def _read(name: str) -> str:
    return get_skill_content(name.removesuffix(".md"))


def test_packaged_artifact_skills_are_trigger_oriented_markdown_guides() -> None:
    for name in ARTIFACT_SKILLS:
        text = _read(name)
        frontmatter = re.match(r"---\n(.*?)\n---", text, re.DOTALL)
        assert frontmatter is not None
        assert "description: Use when" in frontmatter.group(1)
        expected_version = _EXPECTED_ARTIFACT_SKILL_VERSIONS[name]
        assert f"version: {expected_version}" in frontmatter.group(1)
        assert "ralph_submit_md_artifact" in text
        assert "ralph_submit_artifact" not in text


_EXPECTED_ARTIFACT_SKILL_VERSIONS: dict[str, str] = {
    "submit-artifact.md": "2.2.0",
    "submit-plan-artifact.md": "2.2.0",
    "submit-commit-message-artifact.md": "2.2.0",
    "submit-commit-review-artifact.md": "2.2.0",
    "submit-development-result-artifact.md": "3.0.0",
    "submit-development-analysis-decision-artifact.md": "1.0.0",
}


def test_packaged_artifact_skills_reference_only_registered_ralph_tools() -> None:
    known = {tool.value for tool in RalphToolName}
    unknown: dict[str, list[str]] = {}
    for name in ARTIFACT_SKILLS:
        references = set(re.findall(r"\bralph_[a-z0-9_]+", _read(name)))
        missing = sorted(references - known)
        if missing:
            unknown[name] = missing

    assert unknown == {}


def test_plan_skill_teaches_sanity_only_contract() -> None:
    text = _read("submit-plan-artifact.md")

    assert "ralph_submit_md_artifact" in text
    assert "declare_complete" in text
    assert "readable" in text or "Submission checks only" in text


def test_planning_skills_teach_the_sanity_only_contract() -> None:
    for name in PLANNING_SKILLS:
        text = _read(name)
        assert "ralph_submit_md_artifact" in text or "executor-ready" in text
        assert "advisory findings are errors" not in text
        assert "advisory diagnostics" not in text

    format_doc = load_bundled_format_doc("planning_analysis_decision")
    assert format_doc is not None
    assert "Observation:" in format_doc
    assert "Cost:" in format_doc
    assert "Verdict:" in format_doc
    assert "Critical Files omits" not in format_doc


def test_plan_skill_teaches_parallel_and_submission() -> None:
    text = _read("submit-plan-artifact.md")

    assert "Parallel work is the default" in text
    assert "ralph_edit_md_artifact" in text
    assert "ralph_edit_md_plan_step" not in text


def test_development_result_skill_teaches_closed_status_vocabulary() -> None:
    text = _read("submit-development-result-artifact.md")

    assert "closed-vocabulary" in text
    assert "`completed`, `partial`, or `failed`" in text
    assert "unknown status is coerced" not in text


def test_prompt_templates_use_markdown_tools_without_retired_json_vocabulary() -> None:
    registry = TemplateContext.default().registry
    combined = "\n".join(
        registry.get_template(name)
        for name in (
            "planning",
            "planning_analysis",
            "planning_edit",
            "planning_edit_fallback",
            "planning_fallback",
        )
    )

    for variable in (
        "SUBMIT_MD_ARTIFACT_TOOL_REFERENCE",
        "VERIFY_MD_ARTIFACT_TOOL_REFERENCE",
    ):
        assert variable in combined
    assert "EDIT_MD_PLAN_STEP_TOOL_REFERENCE" not in combined
    for retired in (
        "ralph_submit_plan_section",
        "ralph_submit_plan_sections",
        "ralph_validate_draft",
        "ralph_finalize_plan",
        "plan.json",
    ):
        assert retired not in combined


def test_planning_analysis_prompt_requires_cost_element_per_finding() -> None:
    source = TemplateContext.default().registry.get_template("planning_analysis.jinja")
    assert "Cost:" in source, "planning_analysis.jinja must require a `Cost:` element per finding"
    assert "Observation:" in source
    assert "Verdict:" in source
    assert "MCP plan-edit tools" not in source


def _registered_tool_names() -> set[str]:
    return {spec.metadata.definition.name for spec in tool_specs(McpConfig())}


def test_registered_tools_equal_canonical_enum() -> None:
    registered = _registered_tool_names()
    enum_names = {tool.value for tool in RalphToolName}
    assert registered == enum_names, (
        f"registered tools drift from RalphToolName enum: "
        f"only-registered={sorted(registered - enum_names)}, "
        f"only-enum={sorted(enum_names - registered)}"
    )


@pytest.mark.parametrize("drain", list(SessionDrain))
def test_advertised_tools_per_drain_match_registration(drain: SessionDrain) -> None:
    capability_ids = template_variables.default_capability_identifiers_for_drain(drain)
    if not capability_ids:
        pytest.skip(f"drain {drain!r} has no default capabilities")
    visible = set(visible_tool_names_for_capabilities(capability_ids, drain=drain.value))
    registered = _registered_tool_names()
    missing = visible - registered
    assert not missing, (
        f"drain {drain!r} advertises tools that are not registered: {sorted(missing)}"
    )


@pytest.mark.parametrize("drain", list(SessionDrain))
def test_prompt_reference_variables_cover_visible_tools(drain: SessionDrain) -> None:
    capability_ids = template_variables.default_capability_identifiers_for_drain(drain)
    if not capability_ids:
        pytest.skip(f"drain {drain!r} has no default capabilities")
    visible = set(visible_tool_names_for_capabilities(capability_ids, drain=drain.value))
    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    vars_map = template_variables.capability_template_variables(caps, flags)
    enum_member_by_value = {tool.value: tool.name for tool in RalphToolName}
    for tool in sorted(visible):
        member_name = enum_member_by_value.get(tool, tool.upper())
        var_name = f"{member_name}_TOOL_REFERENCE"
        value = vars_map.get(var_name, "")
        assert value, (
            f"drain {drain!r}: visible tool {tool!r} rendered empty "
            f"{var_name}; partial will silently drop the tool name"
        )


@pytest.mark.parametrize("drain", list(SessionDrain))
def test_server_registry_visible_names_equals_rendered_prompt_set(
    drain: SessionDrain,
) -> None:
    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.tool_contract import canonicalize_tool_names
    from ralph.mcp.tools.bridge import build_ralph_tool_registry
    from ralph.workspace.memory import MemoryWorkspace

    capability_ids = template_variables.default_capability_identifiers_for_drain(drain)
    if not capability_ids:
        pytest.skip(f"drain {drain!r} has no default capabilities")
    session = AgentSession(
        session_id=f"ac01-{drain.value}",
        run_id=f"ac01-{drain.value}",
        drain=drain.value,
        capabilities=set(capability_ids),
    )
    workspace = MemoryWorkspace()
    registry = build_ralph_tool_registry(session, workspace)
    server_visible_canonical = {
        str(name)
        for name in canonicalize_tool_names(
            [definition.name for definition in registry.list_definitions()]
        )
    }

    prompt_visible = set(visible_tool_names_for_capabilities(capability_ids, drain=drain.value))
    prompt_canonical = set(canonicalize_tool_names(prompt_visible))

    assert server_visible_canonical == prompt_canonical, (
        f"drain {drain!r}: server registry visible names vs. rendered "
        f"prompt tool set must be equal. "
        f"only-server={sorted(server_visible_canonical - prompt_canonical)}, "
        f"only-prompt={sorted(prompt_canonical - server_visible_canonical)}"
    )

    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    vars_map = template_variables.capability_template_variables(caps, flags)
    enum_member_by_value = {tool.value: tool.name for tool in RalphToolName}
    for tool in sorted(server_visible_canonical):
        member_name = enum_member_by_value.get(tool, tool.upper())
        var_name = f"{member_name}_TOOL_REFERENCE"
        assert vars_map.get(var_name, ""), (
            f"drain {drain!r}: server-visible tool {tool!r} rendered "
            f"empty {var_name} (every server-visible tool MUST have a "
            f"non-empty prompt reference)"
        )


def _render_partial(drain: SessionDrain) -> str:
    from ralph.prompts.template_context import TemplateContext
    from ralph.prompts.template_engine import render_template

    ctx = TemplateContext.default()
    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    variables = template_variables.capability_template_variables(caps, flags)
    partial = ctx.partials["_mcp_tools"]
    return render_template(partial, variables, ctx.partials)


@pytest.mark.parametrize("drain", list(SessionDrain))
def test_mcp_partial_states_edit_tools_only_write_path(drain: SessionDrain) -> None:
    rendered = _render_partial(drain)
    caps, _flags = template_variables.default_caps_and_flags_for_drain(drain)
    has_mcp_write = caps.contains(RalphCapability.WORKSPACE_WRITE_TRACKED)
    if has_mcp_write:
        assert "parent session's write/edit path" in rendered, (
            f"drain {drain!r} has workspace write but the partial does not "
            f"state the edit-tools-only rule"
        )
    else:
        assert "parent session's write/edit path" not in rendered, (
            f"drain {drain!r} has no workspace write but the partial "
            f"still asserts the edit-tools-only rule"
        )


@pytest.mark.parametrize("drain", list(SessionDrain))
def test_mcp_partial_kept_within_two_added_sentences(drain: SessionDrain) -> None:
    rendered = _render_partial(drain)
    caps, _flags = template_variables.default_caps_and_flags_for_drain(drain)
    has_mcp_write = caps.contains(RalphCapability.WORKSPACE_WRITE_TRACKED)
    assert "Use these for parent-session workspace reads and searches" in rendered, (
        f"drain {drain!r}: READ/SEARCH clarifying sentence missing"
    )
    if has_mcp_write:
        assert (
            "These Ralph Workflow edit tools are the parent session's write/edit path" in rendered
        ), f"drain {drain!r}: WRITE clarifying sentence missing"
    read_clarifying_count = rendered.count(
        "Use these for parent-session workspace reads and searches"
    )
    assert read_clarifying_count == 1, (
        f"drain {drain!r}: READ/SEARCH clarifying sentence appears "
        f"{read_clarifying_count} times (expected exactly 1)"
    )
    write_clarifying_count = rendered.count(
        "These Ralph Workflow edit tools are the parent session's write/edit path"
    )
    if has_mcp_write:
        assert write_clarifying_count == 1, (
            f"drain {drain!r}: WRITE clarifying sentence appears "
            f"{write_clarifying_count} times (expected exactly 1)"
        )
    else:
        assert write_clarifying_count == 0, (
            f"drain {drain!r}: WRITE clarifying sentence leaks into a read-only drain's partial"
        )


def test_mcp_partial_identical_across_phase_templates() -> None:
    rendered = _render_partial(SessionDrain.DEVELOPMENT)
    visible = set(
        visible_tool_names_for_capabilities(
            template_variables.default_capability_identifiers_for_drain(SessionDrain.DEVELOPMENT),
            drain=SessionDrain.DEVELOPMENT.value,
        )
    )
    for tool in sorted(visible):
        assert f"`{tool}`" in rendered, f"visible tool {tool!r} not rendered in the shared partial"
    assert "parent session's write/edit path" in rendered
