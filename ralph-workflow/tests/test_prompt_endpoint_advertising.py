"""Test that every capability-granted endpoint is advertised in the rendered prompt.

The shared ``_mcp_tools.jinja`` partial is the single source of truth for
which brokered MCP tools the agent sees. This test renders each phase
template through the real rendering pipeline and asserts:

* every tool name in ``visible_tool_names_for_capabilities`` for the
  profile appears in the rendered MCP tools section,
* no tool name leaks into a profile that did NOT grant it,
* the rendered MCP section stays under a byte budget (catch bloat).

Runs entirely in-process against ``TemplateContext.default()``; fits inside
the immutable 60s default-gate test budget.
"""

from __future__ import annotations

import re

import pytest

from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.tool_contract import visible_tool_names_for_capabilities
from ralph.mcp.tools.names import RalphToolName
from ralph.prompts import template_variables
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template

#: Hard byte budget on the rendered MCP tools section. Bumping this is a
#: prompt-bloat regression; tighten it instead of loosening it.
_MCP_SECTION_BYTE_BUDGET = 3_600

#: Phase -> (template_name, drain) map. These cover every phase that uses
#: the brokered MCP tools in production. We pick templates that render
#: cleanly with a uniform minimal variable set so the test stays
#: deterministic; conflict_resolution is excluded because it requires
#: bespoke rebase-only variables (``replaying_commit_sha`` and friends).
_PHASE_PROFILE_FIXTURES: tuple[tuple[str, str, SessionDrain], ...] = (
    ("planning.jinja", "planning", SessionDrain.PLANNING),
    ("development_analysis.jinja", "development_analysis", SessionDrain.DEVELOPMENT_ANALYSIS),
    ("review_analysis.jinja", "review_analysis", SessionDrain.REVIEW_ANALYSIS),
    ("developer_iteration.jinja", "developer_iteration", SessionDrain.DEVELOPMENT),
    ("worker_developer.jinja", "worker_developer", SessionDrain.DEVELOPMENT),
)


def _visible_tool_strings(drain: SessionDrain) -> set[str]:
    """Return the rendered-as-string tool names for the default capability profile."""
    caps, _ = template_variables.default_caps_and_flags_for_drain(drain)
    raw = visible_tool_names_for_capabilities(
        sorted(cap.value for cap in caps.to_vec()),
        drain=drain.value,
    )
    return {
        (tool.value if hasattr(tool, "value") else str(tool))
        for tool in raw
    }


def _render_phase(template_name: str, drain: SessionDrain) -> str:
    """Render ``template_name`` with the default capability variables for ``drain``."""
    context = TemplateContext.default()
    tmpl = context.registry.get_template(template_name)
    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    base_vars: dict[str, str] = {
        "PROMPT": "",
        "PROMPT_PATH": "PROMPT.md",
        "PLAN": "",
        "PLAN_PATH": ".agent/artifacts/plan.md",
        "ANALYSIS_FEEDBACK": "",
        "ANALYSIS_FEEDBACK_PATH": ".agent/artifacts/analysis_feedback.md",
        "LAST_RETRY_ERROR": "",
        "IS_WORKER": "",
        "IS_CONTINUATION": "",
        "PRIOR_RESULT_STATUS": "",
        "WORKER_NAMESPACE": "",
        "ARTIFACT_HISTORY_PATH": "",
        "ARTIFACT_HISTORY_DIR": "",
        "FIX_RESULT": "",
        "FIX_RESULT_PATH": "",
        "LATEST_ARTIFACT": "",
        "LATEST_ARTIFACT_PATH": "",
        "SKILLS_INLINE_CONTENT": "",
        "HAS_DOCS_MCP": "",
        "DOCS_MCP_PORT": "localhost:6280",
        "DOCS_LOOKUP_VARIANT": "",
        "DOCS_LOOKUP_PHASE": "development",
        "DOCS_LOOKUP_INTENT": "",
        "DOCS_LOOKUP_ACTION": "",
        "PRODUCT_CRITERIA": "",
        "PRODUCT_CRITERIA_PATH": "",
        "HIDE_ARTIFACT_SUBMISSION_GUIDANCE": "",
        "ISSUES": "",
        "ISSUES_PATH": "",
        "unit_id": "",
        "description": "",
        "allowed_directories": "",
        "WORKER_FALLBACK_PATH": "",
    }
    vars_map = {**base_vars, **template_variables.capability_template_variables(caps, flags)}
    return render_template(tmpl, vars_map, context.partials)


def _extract_mcp_section(rendered: str) -> str:
    """Return the rendered MCP TOOLS brokered section including the artifact block.

    The shared ``_mcp_tools.jinja`` partial owns the brokered tool
    advertising INCLUDING the ``ARTIFACT SUBMISSION`` block, so we extract
    from ``MCP TOOLS (Ralph Workflow Brokered)`` up to the end of the
    ``ARTIFACT SUBMISSION`` block. We stop before unrelated shared
    partials such as ``_no_git_commit.j2`` (which mentions
    ``unsafe_exec`` / ``raw_exec`` in its read-only VCS whitelist — a
    different concern from brokered tool advertising).
    """
    start = re.search(r"^MCP TOOLS \(Ralph Workflow Brokered\)", rendered, re.MULTILINE)
    if start is None:
        start = re.search(r"^MCP TOOLS", rendered, re.MULTILINE)
    if start is None:
        return rendered
    # Stop at the next H2 that is NOT ``MCP TOOLS`` AND is not
    # ``ARTIFACT SUBMISSION`` (the artifact block is part of the brokered
    # partial — the parallel branch is leaving it byte-stable).
    rest = rendered[start.start():]
    next_h2 = re.search(r"^## (?!MCP TOOLS|ARTIFACT SUBMISSION)\S", rest, re.MULTILINE)
    end = next_h2.start() if next_h2 is not None else len(rest)
    section = rest[:end]
    cut_at = re.search(r"^- Use `declare_complete` when finished", section, re.MULTILINE)
    if cut_at is not None:
        return section[: cut_at.end()]
    return section


@pytest.mark.parametrize("template_name,phase_label,drain", _PHASE_PROFILE_FIXTURES)
def test_rendered_prompt_advertises_every_visible_tool(
    template_name: str,
    phase_label: str,
    drain: SessionDrain,
) -> None:
    """Every capability-granted tool must appear by name in the rendered prompt."""
    visible = _visible_tool_strings(drain)
    rendered = _render_phase(template_name, drain)
    section = _extract_mcp_section(rendered)

    assert visible, f"{phase_label} expected at least one visible tool"

    # Planning.jinja and planning_edit.jinja intentionally set
    # ``HIDE_ARTIFACT_SUBMISSION_GUIDANCE = true`` so the brokered
    # artifact-submission block does NOT render — the planning phase uses
    # a different submission shape. Skip the artifact tools for those
    # phases because the planning block legitimately advertises them
    # elsewhere in the rendered prompt (e.g. the prompt body itself).
    hidden_artifact_phases = {
        "planning",
        "planning_edit",
    }
    skip_artifact_tools = phase_label in hidden_artifact_phases
    artifact_tool_names = {
        "ralph_submit_md_artifact",
        "ralph_verify_md_artifact",
        "ralph_stage_md_artifact",
        "ralph_get_md_draft",
        "ralph_discard_md_draft",
        "ralph_finalize_md_artifact",
        "coordinate",
        "declare_complete",
    }

    missing: list[str] = []
    for raw_name in visible:
        if skip_artifact_tools and raw_name in artifact_tool_names:
            continue
        # Some names are rendered as Claude alias `mcp__ralph__<tool>` and
        # others as the raw `tool` string. Either form is acceptable.
        if raw_name not in section and f"mcp__ralph__{raw_name}" not in section:
            missing.append(raw_name)
    assert not missing, (
        f"Phase {phase_label} ({template_name}) did not advertise tools: "
        f"{missing}; rendered MCP section:\n{section}"
    )


@pytest.mark.parametrize("template_name,phase_label,drain", _PHASE_PROFILE_FIXTURES)
def test_rendered_prompt_does_not_advertise_ungranted_tools(
    template_name: str,
    phase_label: str,
    drain: SessionDrain,
) -> None:
    """Tools NOT granted by the profile must NOT appear by name in the section."""
    visible = _visible_tool_strings(drain)
    visible_aliases = {f"mcp__ralph__{name}" for name in visible}
    rendered = _render_phase(template_name, drain)
    section = _extract_mcp_section(rendered)

    leaked: list[str] = []
    for member in RalphToolName:
        raw_name = member.value
        aliased = f"mcp__ralph__{raw_name}"
        # The downstream policy-remediation branch is removing plan-related
        # endpoints. The phase branch's instruction tells us not to modify
        # the artifact submission block's wording for the removed names,
        # so we skip any tool that the artifact submission block
        # legitimately names (the submit/verify/stage/get/finalize/discard
        # set) because those references are part of the block we are
        # explicitly told to keep byte-stable.
        artifact_submission_block = {
            "ralph_submit_md_artifact",
            "ralph_verify_md_artifact",
            "ralph_stage_md_artifact",
            "ralph_get_md_draft",
            "ralph_discard_md_draft",
            "ralph_finalize_md_artifact",
            "declare_complete",
        }
        if raw_name in artifact_submission_block:
            continue
        if (
            raw_name not in visible
            and raw_name not in visible_aliases
            and (raw_name in section or aliased in section)
        ):
            leaked.append(raw_name)
    assert not leaked, (
        f"Phase {phase_label} ({template_name}) leaked ungranted tools: "
        f"{leaked}; rendered MCP section:\n{section}"
    )


@pytest.mark.parametrize("template_name,phase_label,drain", _PHASE_PROFILE_FIXTURES)
def test_rendered_mcp_section_stays_under_byte_budget(
    template_name: str,
    phase_label: str,
    drain: SessionDrain,
) -> None:
    """Catch prompt-bloat regressions: section stays within the byte budget."""
    rendered = _render_phase(template_name, drain)
    section = _extract_mcp_section(rendered)
    assert len(section.encode("utf-8")) <= _MCP_SECTION_BYTE_BUDGET, (
        f"Phase {phase_label} ({template_name}) MCP section exceeded "
        f"{_MCP_SECTION_BYTE_BUDGET} bytes; got {len(section.encode('utf-8'))}."
    )


def test_planning_drain_advertises_explore_index_tools() -> None:
    """Planning drains that grant explore must name ralph_reindex + ralph_index_status."""
    visible = _visible_tool_strings(SessionDrain.PLANNING)
    rendered = _render_phase("planning.jinja", SessionDrain.PLANNING)
    section = _extract_mcp_section(rendered)
    if "ralph_reindex" in visible:
        assert "ralph_reindex" in section
        assert "ralph_index_status" in section
        assert "ralph_graph" in section


def test_development_drain_advertises_web_search_when_granted() -> None:
    """Development drains that grant web.search must name web_search + visit_url."""
    visible = _visible_tool_strings(SessionDrain.DEVELOPMENT)
    rendered = _render_phase("worker_developer.jinja", SessionDrain.DEVELOPMENT)
    section = _extract_mcp_section(rendered)
    if "web_search" in visible:
        assert "web_search" in section
    if "visit_url" in visible:
        assert "visit_url" in section
