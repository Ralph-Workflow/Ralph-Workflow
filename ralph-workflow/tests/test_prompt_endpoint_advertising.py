from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import Environment, meta

from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.tool_contract import visible_tool_names_for_capabilities
from ralph.mcp.tools.names import RalphToolName
from ralph.prompts import template_variables
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.template_registry import packaged_template_root

_MCP_SECTION_BYTE_BUDGET = 3_600

_PHASE_PROFILE_FIXTURES: tuple[tuple[str, str, SessionDrain], ...] = (
    ("planning.jinja", "planning", SessionDrain.PLANNING),
    ("development_analysis.jinja", "development_analysis", SessionDrain.DEVELOPMENT_ANALYSIS),
    ("developer_iteration.jinja", "developer_iteration", SessionDrain.DEVELOPMENT),
)


def _visible_tool_strings(drain: SessionDrain) -> set[str]:
    caps, _ = template_variables.default_caps_and_flags_for_drain(drain)
    raw = visible_tool_names_for_capabilities(
        sorted(cap.value for cap in caps.to_vec()),
        drain=drain.value,
    )
    return {(tool.value if hasattr(tool, "value") else str(tool)) for tool in raw}


def _render_phase(template_name: str, drain: SessionDrain) -> str:
    context = TemplateContext.default()
    tmpl = context.registry.get_template(template_name)
    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    base_vars: dict[str, str] = {
        "PRODUCT_CRITERIA": "",
        "PRODUCT_CRITERIA_PATH": "PROMPT.md",
        "PLAN": "",
        "PLAN_PATH": ".agent/artifacts/plan.md",
        "ANALYSIS_FEEDBACK": "",
        "ANALYSIS_FEEDBACK_PATH": ".agent/artifacts/analysis_feedback.md",
        "LAST_RETRY_ERROR": "",
        "IS_CONTINUATION": "",
        "PRIOR_RESULT_STATUS": "",
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
        "HIDE_ARTIFACT_SUBMISSION_GUIDANCE": "",
        "ISSUES": "",
        "ISSUES_PATH": "",
    }
    vars_map = {**base_vars, **template_variables.capability_template_variables(caps, flags)}
    return render_template(tmpl, vars_map, context.partials)


def _extract_mcp_section(rendered: str) -> str:
    start = re.search(r"^MCP TOOLS \(Ralph Workflow Brokered\)", rendered, re.MULTILINE)
    if start is None:
        start = re.search(r"^MCP TOOLS", rendered, re.MULTILINE)
    if start is None:
        return rendered
    rest = rendered[start.start() :]
    next_h2 = re.search(r"^## (?!MCP TOOLS|ARTIFACT SUBMISSION)\S", rest, re.MULTILINE)
    end = next_h2.start() if next_h2 is not None else len(rest)
    section = rest[:end]
    cut_at = re.search(r"^- Use `declare_complete` when finished", section, re.MULTILINE)
    if cut_at is not None:
        return section[: cut_at.end()]
    return section


@pytest.mark.parametrize("template_name,phase_label,drain", _PHASE_PROFILE_FIXTURES)
@pytest.mark.timeout_seconds(2.0)
def test_rendered_prompt_advertises_every_visible_tool(
    template_name: str,
    phase_label: str,
    drain: SessionDrain,
) -> None:
    visible = _visible_tool_strings(drain)
    rendered = _render_phase(template_name, drain)
    section = _extract_mcp_section(rendered)

    assert visible, f"{phase_label} expected at least one visible tool"

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
        "ralph_edit_md_artifact",
        "declare_complete",
    }

    missing: list[str] = []
    for raw_name in visible:
        if skip_artifact_tools and raw_name in artifact_tool_names:
            continue
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
    visible = _visible_tool_strings(drain)
    visible_aliases = {f"mcp__ralph__{name}" for name in visible}
    rendered = _render_phase(template_name, drain)
    section = _extract_mcp_section(rendered)

    leaked: list[str] = []
    for member in RalphToolName:
        raw_name = member.value
        aliased = f"mcp__ralph__{raw_name}"
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
    rendered = _render_phase(template_name, drain)
    section = _extract_mcp_section(rendered)
    assert len(section.encode("utf-8")) <= _MCP_SECTION_BYTE_BUDGET, (
        f"Phase {phase_label} ({template_name}) MCP section exceeded "
        f"{_MCP_SECTION_BYTE_BUDGET} bytes; got {len(section.encode('utf-8'))}."
    )


def test_planning_drain_advertises_explore_index_tools() -> None:
    visible = _visible_tool_strings(SessionDrain.PLANNING)
    rendered = _render_phase("planning.jinja", SessionDrain.PLANNING)
    section = _extract_mcp_section(rendered)
    if "ralph_reindex" in visible:
        assert "ralph_reindex" in section
        assert "ralph_index_status" in section
        assert "ralph_graph" in section


def test_development_drain_advertises_web_search_when_granted() -> None:
    visible = _visible_tool_strings(SessionDrain.DEVELOPMENT)
    rendered = _render_phase("developer_iteration.jinja", SessionDrain.DEVELOPMENT)
    section = _extract_mcp_section(rendered)
    if "web_search" in visible:
        assert "web_search" in section
    if "visit_url" in visible:
        assert "visit_url" in section


_ALL_MCP_INCLUSION_TEMPLATES: tuple[tuple[str, SessionDrain], ...] = (
    ("conflict_resolution", SessionDrain.DEVELOPMENT),
    ("developer_iteration", SessionDrain.DEVELOPMENT),
    ("developer_iteration_continuation", SessionDrain.DEVELOPMENT),
    ("development_analysis", SessionDrain.DEVELOPMENT_ANALYSIS),
    ("planning", SessionDrain.PLANNING),
    ("planning_analysis", SessionDrain.ANALYSIS),
    ("planning_edit", SessionDrain.PLANNING),
)


def _discover_mcp_partial_inclusion_templates() -> tuple[str, ...]:
    root = Path(packaged_template_root())
    included: list[str] = []
    for path in sorted(root.glob("*.jinja")):
        text = path.read_text()
        if "shared/_mcp_tools.j2" in text or "shared/_mcp_tools.jinja" in text:
            included.append(path.stem)
    return tuple(included)


def _render_drain_template(template_stem: str, drain: SessionDrain) -> str:
    context = TemplateContext.default()
    template = context.registry.get_template(template_stem + ".jinja")

    env = Environment()
    referenced: set[str] = set()
    for path in sorted(Path(packaged_template_root()).glob("*.jinja")):
        referenced |= meta.find_undeclared_variables(env.parse(path.read_text()))
    for partial_dir in ("shared/*.jinja", "shared/*.j2"):
        for path in sorted(Path(packaged_template_root()).glob(partial_dir)):
            referenced |= meta.find_undeclared_variables(env.parse(path.read_text()))
    referenced.discard("raise_error")

    base_vars: dict[str, str] = dict.fromkeys(referenced, "")
    base_vars.update(
        {
            "show_plan_edit_guidance": "true",
            "SKILLS_INLINE_CONTENT": "(inline skills)",
            "PRODUCT_CRITERIA": "",
            "PRODUCT_CRITERIA_PATH": ".agent/PRODUCT_CRITERIA.md",
            "PLAN_PATH": ".agent/artifacts/plan.md",
            "ANALYSIS_FEEDBACK_PATH": ".agent/analysis_feedback.md",
            "LATEST_ARTIFACT_PATH": ".agent/artifacts/development_result.md",
            "ISSUES_PATH": ".agent/issues.md",
            "WORKER_FALLBACK_PATH": ".agent/artifacts/worker_development_result.md",
            "ARTIFACT_HISTORY_DIR": ".agent/artifacts",
            "ARTIFACT_HISTORY_PATH": ".agent/artifacts/history.md",
            "FIX_RESULT_PATH": ".agent/artifacts/fix_result.md",
            "DOCS_MCP_PORT": "localhost:6280",
            "DOCS_LOOKUP_PHASE": "development",
            "analysis_feedback_summary": "(none)",
            "CHANGES_PATH": ".agent/changes.md",
            "DIFF_PATH": ".agent/diff.patch",
            "PRIOR_RESULT_MARKDOWN": "(partial body)",
            "PRIOR_SESSION_ID": "session-123",
            "agents_block_begin": "",
            "agents_block_end": "",
            "allowed_directories": "/workspace",
            "canonical_dir": ".agent/artifacts",
            "applicability_overrides_path": ".agent/overrides.md",
            "gate_script_policy_path": ".agent/gates.md",
            "submit_tool_names": "ralph_submit_md_artifact",
            "declare_complete_tool_names": "declare_complete",
            "verify_tool_names": "ralph_verify_md_artifact",
            "approved_tools": "",
            "artifact_type": "development_result",
            "migrated_marker": "",
            "repo_root": "/tmp/repo",
            "target": "main",
            "replaying_commit_sha": "abcdef0123456789",
            "replaying_commit_subject": "(rebase stop)",
            "stop_index": "1",
            "stop_cap": "3",
            "round_index": "1",
            "round_cap": "5",
            "conflicted_block": "(no conflicts)",
            "feedback_block": "",
            "findings_block": "",
        }
    )

    caps, flags = template_variables.default_caps_and_flags_for_drain(drain)
    cap_vars = template_variables.capability_template_variables(caps, flags)
    merged = {**base_vars, **cap_vars}
    return render_template(template, merged, context.partials)


@pytest.mark.parametrize("template_stem,drain", _ALL_MCP_INCLUSION_TEMPLATES)
def test_mcp_partial_inclusion_templates_match_compiled_map(
    template_stem: str, drain: SessionDrain
) -> None:
    discovered = set(_discover_mcp_partial_inclusion_templates())
    compiled = {name for name, _ in _ALL_MCP_INCLUSION_TEMPLATES}
    missing_from_compiled = discovered - compiled
    missing_from_disk = compiled - discovered
    assert not missing_from_compiled, (
        f"discovered templates including shared/_mcp_tools are not "
        f"listed in _ALL_MCP_INCLUSION_TEMPLATES: "
        f"{sorted(missing_from_compiled)}"
    )
    assert not missing_from_disk, (
        f"_ALL_MCP_INCLUSION_TEMPLATES lists templates that no longer "
        f"include shared/_mcp_tools: {sorted(missing_from_disk)}"
    )


@pytest.mark.parametrize("template_stem,drain", _ALL_MCP_INCLUSION_TEMPLATES)
@pytest.mark.timeout_seconds(2.0)
def test_mcp_partial_renders_visible_tools_and_rule_for_every_inclusion(
    template_stem: str, drain: SessionDrain
) -> None:
    visible = _visible_tool_strings(drain)
    rendered = _render_drain_template(template_stem, drain)
    section = _extract_mcp_section(rendered)

    assert visible, f"{template_stem} expected at least one visible tool"

    assert "Use these for parent-session workspace reads and searches" in section, (
        f"{template_stem} MCP section missing the brokered read/search "
        f"clarifying sentence; rendered section:\n{section}"
    )

    artifact_tool_names = {
        "ralph_submit_md_artifact",
        "ralph_verify_md_artifact",
        "ralph_stage_md_artifact",
        "ralph_get_md_draft",
        "ralph_discard_md_draft",
        "ralph_finalize_md_artifact",
        "ralph_edit_md_artifact",
        "declare_complete",
    }
    skip_artifact_tools = template_stem in {
        "planning",
        "planning_edit",
    }

    aliases_visible = {f"mcp__ralph__{name}" for name in visible}
    missing: list[str] = []
    for raw_name in visible:
        if skip_artifact_tools and raw_name in artifact_tool_names:
            continue
        if raw_name not in section and f"mcp__ralph__{raw_name}" not in section:
            missing.append(raw_name)
    assert not missing, (
        f"{template_stem} (drain {drain.value}) did not advertise "
        f"tools: {missing}; rendered MCP section:\n{section}"
    )

    leaked: list[str] = []
    artifact_submission_block = {
        "ralph_submit_md_artifact",
        "ralph_verify_md_artifact",
        "ralph_stage_md_artifact",
        "ralph_get_md_draft",
        "ralph_discard_md_draft",
        "ralph_finalize_md_artifact",
        "declare_complete",
    }
    for member in RalphToolName:
        raw_name = member.value
        aliased = f"mcp__ralph__{raw_name}"
        if raw_name in artifact_submission_block:
            continue
        if (
            raw_name not in visible
            and raw_name not in aliases_visible
            and (raw_name in section or aliased in section)
        ):
            leaked.append(raw_name)
    assert not leaked, (
        f"{template_stem} (drain {drain.value}) leaked ungranted "
        f"tools into the brokered section: {leaked}; rendered MCP "
        f"section:\n{section}"
    )

    caps, _ = template_variables.default_caps_and_flags_for_drain(drain)
    from ralph.mcp.protocol.capability_mapping import (
        Capability as RalphCapability,
    )

    has_mcp_write = caps.contains(RalphCapability.WORKSPACE_WRITE_TRACKED)
    write_clarifying_count = section.count(
        "These Ralph Workflow edit tools are the parent session's write/edit path"
    )
    if has_mcp_write:
        assert write_clarifying_count == 1, (
            f"{template_stem} (drain {drain.value}, write granted) "
            f"rendered the write-only sentence "
            f"{write_clarifying_count} times (expected exactly 1)"
        )
    else:
        assert write_clarifying_count == 0, (
            f"{template_stem} (drain {drain.value}, no write) still "
            f"asserts the edit-tools-only rule"
        )

    universal_brokered_count = section.count(
        "PARENT SESSION ONLY: the brokered-only workspace rule does not apply to native sub-agents"
    )
    assert universal_brokered_count == 1, (
        f"{template_stem} (drain {drain.value}) rendered the universal "
        f"BROKERED-ONLY sentence {universal_brokered_count} times "
        f"(expected exactly 1)"
    )

    assert len(section.encode("utf-8")) <= _MCP_SECTION_BYTE_BUDGET, (
        f"{template_stem} (drain {drain.value}) MCP section exceeded "
        f"{_MCP_SECTION_BYTE_BUDGET} bytes; got {len(section.encode('utf-8'))}."
    )
