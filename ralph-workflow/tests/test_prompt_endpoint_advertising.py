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
        "ralph_edit_md_artifact",
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


# --- AC-04: every shared-partial inclusion path renders identically --------
#
# Every top-level production prompt template that includes the shared
# ``shared/_mcp_tools.j2`` partial must render an MCP tools section
# that contains exactly that drain's visible names AND the
# brokered-only/write-only rule. This guard is the cross-phase
# counterpart to the per-drain
# ``test_mcp_partial_identical_across_phase_templates`` test in
# ``tests/test_internal_skills_mcp_prompts.py``: that test pins the
# partial source itself; this test pins every inclusion path through
# the full production template set so a phase that drops the include
# or renders a divergent section fails closed at its own template.

#: Mapping every top-level production template that includes
#: ``shared/_mcp_tools.j2`` to the drain it ships with. Discovered
#: by :func:`_discover_mcp_partial_inclusion_templates`; the
#: constant here is the canonical comparison surface.
_ALL_MCP_INCLUSION_TEMPLATES: tuple[tuple[str, SessionDrain], ...] = (
    ("conflict_resolution", SessionDrain.DEVELOPMENT),
    ("developer_iteration", SessionDrain.DEVELOPMENT),
    ("developer_iteration_continuation", SessionDrain.DEVELOPMENT),
    ("development_analysis", SessionDrain.DEVELOPMENT_ANALYSIS),
    ("fix_mode", SessionDrain.FIX),
    ("planning", SessionDrain.PLANNING),
    ("planning_analysis", SessionDrain.ANALYSIS),
    ("planning_edit", SessionDrain.PLANNING),
    ("review", SessionDrain.REVIEW),
    ("review_analysis", SessionDrain.REVIEW_ANALYSIS),
    ("worker_developer", SessionDrain.DEVELOPMENT),
)


def _discover_mcp_partial_inclusion_templates() -> tuple[str, ...]:
    """Return the stem of every top-level template that includes the MCP partial.

    Drives the regression's discovery path so a future template
    dropped in or removed is caught immediately by
    :func:`test_mcp_partial_inclusion_templates_match_compiled_map`.
    """
    root = Path(packaged_template_root())
    included: list[str] = []
    for path in sorted(root.glob("*.jinja")):
        text = path.read_text()
        if "shared/_mcp_tools.j2" in text or "shared/_mcp_tools.jinja" in text:
            included.append(path.stem)
    return tuple(included)


def _render_drain_template(template_stem: str, drain: SessionDrain) -> str:
    """Render ``template_stem.jinja`` with the drain's default caps.

    Unlike :func:`_render_phase`, this helper works for every
    template that includes the partial — not just the five the
    legacy fixture covered — by using the AST-discovered universe
    of variables and a wider set of realistic defaults for the
    bespoke variables each template wants (e.g.
    ``replaying_commit_sha`` for conflict_resolution).
    """
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
    # Realistic defaults for bespoke variables each template wants.
    base_vars.update(
        {
            "show_plan_edit_guidance": "true",
            "SKILLS_INLINE_CONTENT": "(inline skills)",
            "PROMPT_PATH": ".agent/PRODUCT_CRITERIA.md",
            "PLAN_PATH": ".agent/artifacts/plan.md",
            "ANALYSIS_FEEDBACK_PATH": ".agent/analysis_feedback.md",
            "LATEST_ARTIFACT_PATH": ".agent/artifacts/development_result.md",
            "ISSUES_PATH": ".agent/issues.md",
            "PRODUCT_CRITERIA_PATH": ".agent/PRODUCT_CRITERIA.md",
            "WORKER_FALLBACK_PATH": ".agent/artifacts/worker_development_result.md",
            "ARTIFACT_HISTORY_DIR": ".agent/artifacts",
            "ARTIFACT_HISTORY_PATH": ".agent/artifacts/history.md",
            "FIX_RESULT_PATH": ".agent/artifacts/fix_result.md",
            "DOCS_MCP_PORT": "localhost:6280",
            "DOCS_LOOKUP_PHASE": "development",
            "analysis_feedback_summary": "(none)",
            "CHANGES_PATH": ".agent/changes.md",
            "DIFF_PATH": ".agent/diff.patch",
            "PRIOR_RESULT_SUMMARY": "(partial)",
            "PRIOR_RESULT_NEXT_STEPS": "(continue)",
            "PRIOR_RESULT_CONTINUATION": "true",
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
    """All compilED inclusion entries are present in the discovered set; nothing missing.

    Pins the ``_ALL_MCP_INCLUSION_TEMPLATES`` constant against
    on-disk reality: every entry must be a real top-level template
    (compilED map entry is discovered) and every discovered
    template that includes the partial must appear as an
    entry. A mismatch indicates the compiled map drifted from
    the live include sites — a regression guard against the
    partial being added or removed without updating the test.
    """
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
def test_mcp_partial_renders_visible_tools_and_rule_for_every_inclusion(
    template_stem: str, drain: SessionDrain
) -> None:
    """Every inclusion-path renders the drain's visible tools plus the brokered-only rule.

    AC-04 / S-6 demands the shared partial renders identically
    across every phase template that includes it. The test renders
    every entry of :data:`_ALL_MCP_INCLUSION_TEMPLATES` with its
    real drain's default capabilities + an AST-discovered
    fallback variable set, extracts the rendered MCP section via
    :func:`_extract_mcp_section`, and asserts:

    * every canonical tool name visible on the drain is present
      in the section,
    * no canonical tool name visible ONLY to a different drain
      has leaked in,
    * the brokered-only-write-path sentence is rendered when the
      drain grants write (and absent when it does not), and
    * the read/SEARCH clarifying sentence is rendered exactly once.

    A regression in any one template fails closed at that
    template; a regression that drops or duplicates the partial
    fails closed via the discovery test above.
    """
    visible = _visible_tool_strings(drain)
    rendered = _render_drain_template(template_stem, drain)
    section = _extract_mcp_section(rendered)

    assert visible, f"{template_stem} expected at least one visible tool"

    # planning.jinja and planning_edit.jinja intentionally set
    # ``HIDE_ARTIFACT_SUBMISSION_GUIDANCE = true`` so the brokered
    # artifact-submission block does NOT render. The test still
    # works because every other phase template renders the block
    # and ``_extract_mcp_section`` then already includes it; for
    # those phases the artifact-tools assertion below accepts the
    # placeholder names elsewhere in the prompt body. We therefore
    # require the read/SEARCH clarifying line regardless.
    assert "Use these for every workspace read or search" in section, (
        f"{template_stem} MCP section missing the brokered read/search "
        f"clarifying sentence; rendered section:\n{section}"
    )

    # planning-phase templates (planning.jinja, planning_edit.jinja)
    # intentionally set ``HIDE_ARTIFACT_SUBMISSION_GUIDANCE = true``
    # so the brokered artifact-submission block does NOT render —
    # the planning phase uses a different submission shape and
    # advertises the artifact tools elsewhere in the prompt body.
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

    # Every visible tool name must appear by name or its Claude
    # alias. We assert every ENUM member, not just the visible set,
    # so a regression that swaps a tool reference for a non-existent
    # name is caught.
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

    # No tool NOT granted by the drain appears by raw name or
    # alias — the brokered-only rule states the partial must not
    # advertise tools the session cannot call.
    leaked: list[str] = []
    # Artifact submission block is intentionally part of the
    # brokered partial and stays byte-stable per the parallel
    # branch. Its ``ralph_submit_md_artifact`` etc. names render
    # unconditionally in non-planning phases; pin them out of the
    # leak assertion.
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

    # The brokered-only write path sentence must render exactly
    # once when write is granted and not at all when write is not.
    caps, _ = template_variables.default_caps_and_flags_for_drain(drain)
    from ralph.mcp.protocol.capability_mapping import (
        Capability as RalphCapability,
    )

    has_mcp_write = caps.contains(RalphCapability.WORKSPACE_WRITE_TRACKED)
    write_clarifying_count = section.count(
        "These Ralph Workflow edit tools are the ONLY permitted write/edit path"
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

    # The UNIVERSAL brokered-only sentence must render exactly
    # once for every inclusion template — this is the AC-02 / S-4
    # guarantee that read-only phases and write-granted phases
    # both tell the agent the brokered path is the only permitted
    # one. A regression that drops the universal sentence leaves
    # a read-only phase unable to reject native write tools.
    universal_brokered_count = section.count(
        "BROKERED-ONLY: Ralph Workflow's brokered tools are the only permitted workspace path"
    )
    assert universal_brokered_count == 1, (
        f"{template_stem} (drain {drain.value}) rendered the universal "
        f"BROKERED-ONLY sentence {universal_brokered_count} times "
        f"(expected exactly 1)"
    )

    # The MCP section is bounded: existing byte budget for the
    # 5-phase set must also hold for the 10-phase set; we re-apply
    # it here to catch bloat in any newly-rendered phase.
    assert len(section.encode("utf-8")) <= _MCP_SECTION_BYTE_BUDGET, (
        f"{template_stem} (drain {drain.value}) MCP section exceeded "
        f"{_MCP_SECTION_BYTE_BUDGET} bytes; got {len(section.encode('utf-8'))}."
    )
