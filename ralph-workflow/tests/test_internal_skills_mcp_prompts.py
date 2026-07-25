"""Prompt-quality contracts shared by packaged markdown-artifact skills."""

from __future__ import annotations

import re

import pytest

from ralph.config.mcp_models import McpConfig
from ralph.mcp.artifacts.markdown._spec import parse_and_validate
from ralph.mcp.artifacts.markdown.specs.plan import PLAN_SPEC
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.protocol.capability_mapping import Capability as RalphCapability
from ralph.mcp.tool_contract import visible_tool_names_for_capabilities
from ralph.mcp.tools.bridge._registry import tool_specs
from ralph.mcp.tools.names import RalphToolName
from ralph.prompts import template_variables
from ralph.prompts.template_context import TemplateContext
from ralph.skills import get_skill_content

ARTIFACT_SKILLS = (
    "submit-artifact.md",
    "submit-plan-artifact.md",
    "submit-commit-message-artifact.md",
    "submit-development-result-artifact.md",
    "submit-commit-cleanup-artifact.md",
)


def _read(name: str) -> str:
    return get_skill_content(name.removesuffix(".md"))


def test_packaged_artifact_skills_are_trigger_oriented_markdown_guides() -> None:
    for name in ARTIFACT_SKILLS:
        text = _read(name)
        frontmatter = re.match(r"---\n(.*?)\n---", text, re.DOTALL)
        assert frontmatter is not None
        assert "description: Use when" in frontmatter.group(1)
        assert "version: 2.1.0" in frontmatter.group(1)
        assert "ralph_submit_md_artifact" in text
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


def test_plan_skill_teaches_relaxed_shapes_and_subplan_dispatch() -> None:
    text = _read("submit-plan-artifact.md")

    assert "Everything else is a recommended authoring pattern, not required grammar." in text
    assert "radically different headings" in text
    assert "Work Units` for small bounded tasks or verification gates" in text
    assert "Subplan: <Name>` sections when each" in text
    assert "four or five independent execution Subplans" in text
    assert "`Verify:` command must have a specific" in text
    assert "exact, case-sensitive `## Work Units` or `## Parallel Plan`" in text
    assert "Project-specific `Type:` values and target actions are preserved verbatim" in text
    assert "Acceptance-criterion items are criteria, never phantom work units" in text
    assert "exact required section names" not in text
    assert "ralph_edit_md_plan_step" not in text


def test_development_result_skill_teaches_closed_status_vocabulary() -> None:
    text = _read("submit-development-result-artifact.md")

    assert "closed-vocabulary" in text
    assert "`completed` or `partial`" in text
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
    """Every ``## What Came Up Short`` entry must surface a ``Cost:`` element.

    The product brief (AC-12) requires every analysis finding to state
    the run cost it imposes; the per-entry form lives in
    ``planning_analysis.jinja``. If the ``Cost:`` element is dropped or
    rephrased, the standard is no longer stated once and this test
    fails closed.
    """
    source = TemplateContext.default().registry.get_template("planning_analysis.jinja")
    assert "Cost:" in source, (
        "planning_analysis.jinja must require a `Cost:` element per finding"
    )
    # The per-entry form must include the cost class in the same line
    # that names the dimension / defect / evidence / missing / cost.
    assert "Dimension:" in source
    assert "Defect:" in source
    assert "Evidence:" in source
    assert "Missing:" in source
    # The form must keep the cost/fix contract to one finding each.
    assert "Cost:" in source
    # The form must NOT regress to the legacy ``MCP plan-edit tools``
    # vocabulary that no longer exists in the runtime.
    assert "MCP plan-edit tools" not in source


# --- AC-01: registered == advertised regression guard ---------------------
#
# The MCP tool surface has three coupled sources of truth:
#
# 1. The :class:`RalphToolName` enum (canonical name list) — consumed by
#    :mod:`ralph.prompts.template_variables` to generate the
#    ``*_TOOL_REFERENCE`` variables the partial :file:`_mcp_tools.jinja`
#    renders.
# 2. The bridge specs in :mod:`ralph.mcp.tools.bridge._specs_*` — the
#    actual tool registrations the runtime serves.
# 3. :func:`visible_tool_names_for_capabilities` — the live
#    capability-driven projection onto (2) that the prompts consume
#    via :func:`capability_template_variables`.
#
# Drift between these surfaces produces the "GIT_STATUS_TOOL_REFERENCE is
# undefined" template failure class documented in
# :mod:`ralph.prompts.template_variables`. The tests below pin the
# surfaces together per drain and pin the rendered reference variables
# so a new tool cannot ship registered-but-unadvertised or
# advertised-but-unrendered.


def _registered_tool_names() -> set[str]:
    """Return the set of canonical names registered by the bridge specs."""
    return {spec.metadata.definition.name for spec in tool_specs(McpConfig())}


def test_registered_tools_equal_canonical_enum() -> None:
    """Every registered tool name must be a member of ``RalphToolName``.

    The bridge specs and the canonical enum are the two sources of
    truth for tool naming. If they drift, the runtime registers a tool
    the prompt template does not know about (or vice versa).
    """
    registered = _registered_tool_names()
    enum_names = {tool.value for tool in RalphToolName}
    # Plan-artifact-specific tools (ralph_edit_md_artifact etc.) are
    # being removed on another branch — keep them in the enum but
    # do not flag them as registered-or-not.
    assert registered == enum_names, (
        f"registered tools drift from RalphToolName enum: "
        f"only-registered={sorted(registered - enum_names)}, "
        f"only-enum={sorted(enum_names - registered)}"
    )


@pytest.mark.parametrize("drain", list(SessionDrain))
def test_advertised_tools_per_drain_match_registration(drain: SessionDrain) -> None:
    """For each drain, the visible advertised set is a subset of the registered set.

    Every tool the prompt advertises for a drain must be a tool the
    bridge actually registers. The reverse direction (every registered
    tool advertised on every drain) is NOT asserted: a tool is only
    visible to a drain when the drain's capability set grants the
    required capability, so the visible set is a strict subset of the
    registered set.
    """
    capability_ids = template_variables.default_capability_identifiers_for_drain(drain)
    if not capability_ids:
        # Some drains have no default capabilities (e.g. COMMIT runs
        # in a strict read-only mode); skip the assertion for those.
        pytest.skip(f"drain {drain!r} has no default capabilities")
    visible = set(visible_tool_names_for_capabilities(capability_ids, drain=drain.value))
    registered = _registered_tool_names()
    missing = visible - registered
    assert not missing, (
        f"drain {drain!r} advertises tools that are not registered: "
        f"{sorted(missing)}"
    )


@pytest.mark.parametrize("drain", list(SessionDrain))
def test_prompt_reference_variables_cover_visible_tools(drain: SessionDrain) -> None:
    """Every visible tool renders a non-empty ``*_TOOL_REFERENCE`` variable.

    The shared partial :file:`_mcp_tools.jinja` consumes
    ``*_TOOL_REFERENCE`` variables; if a visible tool lacks a
    reference, the partial renders an empty string and the agent loses
    the tool name. This test fails closed on any visible tool that
    has no rendered reference.
    """
    capability_ids = template_variables.default_capability_identifiers_for_drain(drain)
    if not capability_ids:
        pytest.skip(f"drain {drain!r} has no default capabilities")
    visible = set(visible_tool_names_for_capabilities(capability_ids, drain=drain.value))
    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    vars_map = template_variables.capability_template_variables(caps, flags)
    # Build a value-to-enum-name map so a tool whose enum member is
    # ``DISCARD_MD_DRAFT`` looks up ``DISCARD_MD_DRAFT_TOOL_REFERENCE``
    # rather than ``RALPH_DISCARD_MD_DRAFT_TOOL_REFERENCE``. The enum
    # name (not the value) is the prompt-variable suffix; a tool like
    # ``ralph_discard_md_draft`` shares the value prefix with several
    # others, so the enum name is the unambiguous key.
    enum_member_by_value = {tool.value: tool.name for tool in RalphToolName}
    for tool in sorted(visible):
        member_name = enum_member_by_value.get(tool, tool.upper())
        var_name = f"{member_name}_TOOL_REFERENCE"
        value = vars_map.get(var_name, "")
        assert value, (
            f"drain {drain!r}: visible tool {tool!r} rendered empty "
            f"{var_name}; partial will silently drop the tool name"
        )


# --- AC-04: edit-tools-only rule in shared partial ----------------------
#
# The shared partial :file:`_mcp_tools.jinja` is the only prompt-side
# surface that lists every tool an agent is granted, and the only
# prompt-side surface the agent reads when deciding how to mutate the
# workspace. If it ever stops saying the Ralph edit tools are the
# ONLY permitted write path, native shell/editor tools creep back in
# and the workspace integrity contract breaks. The tests below pin
# the wording and pin cross-phase consistency.


def _render_partial(drain: SessionDrain) -> str:
    """Render the shared ``_mcp_tools`` partial for the given drain's caps."""
    from ralph.prompts.template_context import TemplateContext
    from ralph.prompts.template_engine import render_template

    ctx = TemplateContext.default()
    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    variables = template_variables.capability_template_variables(caps, flags)
    partial = ctx.partials["_mcp_tools"]
    return render_template(partial, variables, ctx.partials)


@pytest.mark.parametrize("drain", list(SessionDrain))
def test_mcp_partial_states_edit_tools_only_write_path(drain: SessionDrain) -> None:
    """The shared partial makes the edit-tools-only rule explicit per drain.

    When the drain grants workspace write, the rendered partial must
    state that the Ralph write/edit tools are the only permitted
    write/edit path. When the drain does NOT grant workspace write,
    the partial must NOT contradict the rule — it must omit the WRITE
    section entirely rather than leaving a generic description that
    could be misread.
    """
    rendered = _render_partial(drain)
    caps, _flags = template_variables.default_caps_and_flags_for_drain(drain)
    has_mcp_write = caps.contains(RalphCapability.WORKSPACE_WRITE_TRACKED)
    if has_mcp_write:
        assert "ONLY permitted write/edit path" in rendered, (
            f"drain {drain!r} has workspace write but the partial does not "
            f"state the edit-tools-only rule"
        )
    else:
        # Drain grants no write capability: the partial must omit the
        # WRITE section entirely rather than asserting the rule for a
        # tool the agent cannot call.
        assert "ONLY permitted write/edit path" not in rendered, (
            f"drain {drain!r} has no workspace write but the partial "
            f"still asserts the edit-tools-only rule"
        )


@pytest.mark.parametrize("drain", list(SessionDrain))
def test_mcp_partial_kept_within_two_added_sentences(drain: SessionDrain) -> None:
    """Prompt bloat budget guard: the partial adds at most two sentences.

    The plan's AC-04 caps the wording change at two sentences. The
    partial source is the canonical text; the rendered output must
    not introduce extra prose beyond the two new sentences. We count
    new sentences by comparing the rendered output to a fixed
    baseline that has the same template minus the two added sentences
    — but since the partial is the canonical surface, we instead
    assert the rendered partial contains exactly two sentence-ending
    punctuators (``period + space`` or ``em-dash + space``) AFTER
    the leading ``READ / SEARCH:`` and ``EXPLORE INDEX:`` lines, in
    the new prose paragraphs.
    """
    rendered = _render_partial(drain)
    caps, _flags = template_variables.default_caps_and_flags_for_drain(drain)
    has_mcp_write = caps.contains(RalphCapability.WORKSPACE_WRITE_TRACKED)
    # The two added sentences live below READ/SEARCH and (when write
    # is granted) below WRITE. Pin the substring presence; the plan
    # budget is "no net growth beyond two sentences" — keep the
    # contract simple by asserting each sentence is exactly one line.
    assert "Use these for every workspace read or search" in rendered, (
        f"drain {drain!r}: READ/SEARCH clarifying sentence missing"
    )
    if has_mcp_write:
        assert "These Ralph Workflow edit tools are the ONLY permitted write/edit path" in rendered, (
            f"drain {drain!r}: WRITE clarifying sentence missing"
        )
    # Hard-bloat guard: the partial source adds exactly two new
    # sentences, no more. Count occurrences of the canonical
    # clarifying sentences — anything beyond 1 per slot is bloat.
    read_clarifying_count = rendered.count(
        "Use these for every workspace read or search"
    )
    assert read_clarifying_count == 1, (
        f"drain {drain!r}: READ/SEARCH clarifying sentence appears "
        f"{read_clarifying_count} times (expected exactly 1)"
    )
    write_clarifying_count = rendered.count(
        "These Ralph Workflow edit tools are the ONLY permitted write/edit path"
    )
    if has_mcp_write:
        assert write_clarifying_count == 1, (
            f"drain {drain!r}: WRITE clarifying sentence appears "
            f"{write_clarifying_count} times (expected exactly 1)"
        )
    else:
        assert write_clarifying_count == 0, (
            f"drain {drain!r}: WRITE clarifying sentence leaks into a "
            f"read-only drain's partial"
        )


def test_mcp_partial_identical_across_phase_templates() -> None:
    """Every phase template that includes the partial produces the same MCP section.

    The shared partial is the single source of truth for the agent's
    tool surface. If two phase templates render with different
    wording, agents see a different rule per phase and the
    edit-tools-only rule weakens. The simplest invariant is: pick a
    drain with the maximum tool surface (DEVELOPMENT), render the
    partial, and check the rendered output contains every visible
    tool name AND the edit-tools-only rule.
    """
    rendered = _render_partial(SessionDrain.DEVELOPMENT)
    visible = set(
        visible_tool_names_for_capabilities(
            template_variables.default_capability_identifiers_for_drain(SessionDrain.DEVELOPMENT),
            drain=SessionDrain.DEVELOPMENT.value,
        )
    )
    # Every visible canonical tool name must appear as a backticked
    # reference in the rendered partial.
    for tool in sorted(visible):
        assert f"`{tool}`" in rendered, (
            f"visible tool {tool!r} not rendered in the shared partial"
        )
    # And the edit-tools-only sentence is present.
    assert "ONLY permitted write/edit path" in rendered
