from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import (
    FROZEN_UNIVERSAL_SKILLS,
    SessionCapabilities,
    SessionDrain,
    capability_template_variables,
    skill_template_variables,
)
from ralph.skills import get_skill_content

if TYPE_CHECKING:
    from collections.abc import Mapping

_POLICY_WINS = (
    "Shipped skills provide implementation technique; project policy and workflow "
    "instructions remain authoritative on any conflict."
)
_ANTISLOP_SESSION_CUE = (
    "Use antislop during this session, with its mode pre-resolved for unattended work "
    "so it does not pause for input."
)
_ARTIFACT_SKILL_NAMES = frozenset(
    {
        "submit-artifact",
        "submit-commit-message-artifact",
        "submit-commit-review-artifact",
        "submit-design-verdict-artifact",
        "submit-development-analysis-decision-artifact",
        "submit-development-result-artifact",
        "submit-fix-result-artifact",
        "submit-issues-artifact",
        "submit-plan-artifact",
        "submit-planning-analysis-decision-artifact",
        "submit-policy-remediation-analysis-decision-artifact",
        "submit-product-spec-artifact",
        "submit-review-analysis-decision-artifact",
        "submit-run-time-report-artifact",
        "submit-smoke-test-result-artifact",
    }
)
_PHASE_DRAIN = {
    "commit_message.jinja": SessionDrain.COMMIT,
    "commit_review.jinja": SessionDrain.COMMIT,
    "commit_simplified.jinja": SessionDrain.COMMIT,
    "conflict_resolution.jinja": SessionDrain.DEVELOPMENT,
    "developer_iteration.jinja": SessionDrain.DEVELOPMENT,
    "developer_iteration_continuation.jinja": SessionDrain.DEVELOPMENT,
    "developer_iteration_fallback.jinja": SessionDrain.DEVELOPMENT,
    "development_analysis.jinja": SessionDrain.DEVELOPMENT_ANALYSIS,
    "planning.jinja": SessionDrain.PLANNING,
    "planning_analysis.jinja": SessionDrain.ANALYSIS,
    "planning_edit.jinja": SessionDrain.PLANNING,
    "planning_edit_fallback.jinja": SessionDrain.PLANNING,
    "planning_fallback.jinja": SessionDrain.PLANNING,
    "policy_remediation.jinja": SessionDrain.DEVELOPMENT,
    "policy_remediation_analysis.jinja": SessionDrain.ANALYSIS,
}
_PHASE_REQUIRED_SKILLS = {
    "commit_message.jinja": (),
    "commit_review.jinja": (),
    "commit_simplified.jinja": (),
    "conflict_resolution.jinja": (
        "systematic-debugging",
        "verification-before-completion",
    ),
    "developer_iteration.jinja": FROZEN_UNIVERSAL_SKILLS,
    "developer_iteration_continuation.jinja": FROZEN_UNIVERSAL_SKILLS,
    "developer_iteration_fallback.jinja": FROZEN_UNIVERSAL_SKILLS,
    "development_analysis.jinja": (
        "systematic-debugging",
        "verification-before-completion",
    ),
    "planning.jinja": (
        "test-driven-development",
        "systematic-debugging",
        "verification-before-completion",
    ),
    "planning_analysis.jinja": ("verification-before-completion",),
    "planning_edit.jinja": (
        "test-driven-development",
        "systematic-debugging",
        "verification-before-completion",
    ),
    "planning_edit_fallback.jinja": (
        "test-driven-development",
        "systematic-debugging",
        "verification-before-completion",
    ),
    "planning_fallback.jinja": (
        "test-driven-development",
        "systematic-debugging",
        "verification-before-completion",
    ),
    "policy_remediation.jinja": (
        "systematic-debugging",
        "verification-before-completion",
    ),
    "policy_remediation_analysis.jinja": (
        "systematic-debugging",
        "verification-before-completion",
    ),
}
_PHASE_FALLBACK_HINTS = {
    "conflict_resolution.jinja": (
        "Analyze the root intent of conflicting changes on both branches",
        "Confirm resolved files build and pass checks cleanly",
    ),
    "developer_iteration.jinja": (
        "Write failing tests before writing implementation code",
        "Isolate the root cause before attempting fixes",
        "Keep user interfaces concise and direct",
        "Run fresh verification commands before claiming work is complete",
    ),
    "developer_iteration_continuation.jinja": (
        "Write failing tests before writing implementation code",
        "Isolate the root cause before attempting fixes",
        "Keep user interfaces concise and direct",
        "Run fresh verification commands before claiming work is complete",
    ),
    "developer_iteration_fallback.jinja": (
        "Write failing tests before writing implementation code",
        "Isolate the root cause before attempting fixes",
        "Keep user interfaces concise and direct",
        "Run fresh verification commands before claiming work is complete",
    ),
    "development_analysis.jinja": (
        "Verify claims independently with fresh command execution",
        "Trace failing checks to their root causes",
    ),
    "planning.jinja": ("Mention task-relevant available skills when they help the executor",),
    "planning_analysis.jinja": (
        "Check the plan's claims against fresh evidence rather than the planner's account",
    ),
    "planning_edit.jinja": ("Mention task-relevant available skills when they help the executor",),
    "planning_edit_fallback.jinja": (
        "Mention task-relevant available skills when they help the executor",
    ),
    "planning_fallback.jinja": (
        "Mention task-relevant available skills when they help the executor",
    ),
    "policy_remediation.jinja": (
        "Probe declared markers and gates with reproducible command execution",
        "Trace failing checks or policy validator errors to their root discrepancy",
    ),
    "policy_remediation_analysis.jinja": (
        "Probe declared markers and gates with fresh command runs",
        "Isolate the root cause of failing policy gates or validator discrepancies",
    ),
}
_CUE_NAME_RE = re.compile(
    r"\b(?:Use|Name)\s+([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\s+"
    r"(?:before|during|for|when)\b"
)
_ARTIFACT_POINTER_RE = re.compile(r"render_optional_artifact_skill_pointer\(\s*['\"]([^'\"]+)['\"]")
_UNIVERSAL_VARIABLE_RE = re.compile(r"SKILL_INSTALLED_([A-Z0-9_]+)")
_EMPHATIC_RE = re.compile(r"\b(?:CRITICAL|MUST|NEVER)\b")


@pytest.fixture
def all_installed_vars() -> dict[str, str]:
    return skill_template_variables(FROZEN_UNIVERSAL_SKILLS)


@pytest.fixture
def none_installed_vars() -> dict[str, str]:
    return skill_template_variables(())


def _base_variables(template_name: str) -> dict[str, str]:
    session_caps = SessionCapabilities.defaults_for_drain(_PHASE_DRAIN[template_name])
    return {
        **capability_template_variables(
            session_caps.capabilities,
            session_caps.policy_flags,
        ),
        "ANALYSIS_FEEDBACK": "",
        "ANALYSIS_FEEDBACK_PATH": "",
        "ANALYSIS_FEEDBACK_STATUS": "",
        "ARTIFACT_HISTORY_DIR": "",
        "ARTIFACT_HISTORY_PATH": "",
        "DELETE_DECISION_RULES": "No action.",
        "DIFF": "",
        "DIFF_PATH": "",
        "DOCS_LOOKUP_ACTION": "",
        "DOCS_LOOKUP_INTENT": "",
        "DOCS_LOOKUP_PHASE": "",
        "DOCS_LOOKUP_VARIANT": "",
        "HAS_DOCS_MCP": "",
        "HIDE_ARTIFACT_SUBMISSION_GUIDANCE": "true",
        "IS_CONTINUATION": "",
        "ISSUES": "",
        "ISSUES_PATH": "",
        "LAST_RETRY_ERROR": "",
        "MESSAGE_BUDGET": "one concise paragraph",
        "OPTIONAL_CONTEXT_PATHS": "",
        "PLAN": "1. Repair the defect\n- Verify: pytest -q",
        "PLAN_PATH": ".agent/PLAN.md",
        "PRIOR_RESULT_MARKDOWN": "",
        "PRIOR_RESULT_STATUS": "",
        "PRIOR_SESSION_ID": "",
        "PRODUCT_CRITERIA": "Repair the validation failure.",
        "PRODUCT_CRITERIA_PATH": "PROMPT.md",
        "SKILLS_INLINE_CONTENT": "",
        "SUBMIT_MD_ARTIFACT_TOOL_INSTRUCTIONS": ("Submit with ralph_submit_md_artifact."),
        "VERIFICATION_FACTS": "none recorded",
        "agents_block_begin": "AGENTS BEGIN",
        "agents_block_end": "AGENTS END",
        "analysis_feedback_block": "",
        "analysis_feedback_status": "",
        "analysis_feedback_summary": "",
        "approved_tools": "python",
        "artifact_type": "policy_remediation_analysis_decision",
        "canonical_dir": "docs/ralph-workflow-policy",
        "conflicted_block": "src/example.py",
        "declare_complete_tool_names": "declare_complete",
        "feedback_block": "",
        "findings_block": "No findings.",
        "gate_script_policy_path": "docs/gates.md",
        "migrated_marker": "RALPH-WORKFLOW-POLICY",
        "repo_root": "/repo",
        "replaying_commit_sha": "",
        "replaying_commit_subject": "",
        "round_cap": "5",
        "round_index": "1",
        "stop_cap": "5",
        "stop_index": "1",
        "strategy_history": "",
        "submit_tool_names": "ralph_submit_md_artifact",
        "target": "main",
        "verify_tool_names": "ralph_verify_md_artifact",
    }


def _render_phase(
    template_name: str,
    skill_variables: Mapping[str, str],
) -> str:
    context = TemplateContext.default()
    variables = {
        **_base_variables(template_name),
        **skill_variables,
    }
    return render_template(
        context.registry.get_template(template_name),
        variables,
        context.partials,
    )


def _template_sources() -> dict[str, str]:
    context = TemplateContext.default()
    sources = {name: context.registry.get_template(name) for name in _PHASE_REQUIRED_SKILLS}
    sources.update(context.partials)
    return sources


def test_submit_plan_artifact_has_executor_skills_handoff_line() -> None:
    content = get_skill_content("submit-plan-artifact")
    assert (
        "Name executor skills (`test-driven-development`, `systematic-debugging`, "
        "`verification-before-completion`) for appropriate plan steps; the executor "
        "tolerates uninstalled skills." in content
    )


def test_shipped_skills_contains_one_central_policy_wins_statement() -> None:
    context = TemplateContext.default()
    source = context.partials["shared/_shipped_skills"]
    assert source.count(_POLICY_WINS) == 1

    rendered = render_template(source, {}, context.partials)
    assert _POLICY_WINS in rendered


def test_unattended_mode_neutralizes_antislop_when_installed(
    all_installed_vars: dict[str, str],
    none_installed_vars: dict[str, str],
) -> None:
    context = TemplateContext.default()
    harness = (
        "{% from 'shared/_unattended_mode.j2' import render_antislop_unattended_mode %}"
        "{{ render_antislop_unattended_mode() }}"
    )

    rendered_installed = render_template(harness, all_installed_vars, context.partials)
    assert _ANTISLOP_SESSION_CUE in rendered_installed
    assert "Use antislop during for this session." not in rendered_installed

    rendered_absent = render_template(harness, none_installed_vars, context.partials)
    assert "antislop" not in rendered_absent


@pytest.mark.parametrize(
    ("template_name", "required_skills"),
    _PHASE_REQUIRED_SKILLS.items(),
)
def test_phase_prompt_names_only_its_installed_universal_skills(
    template_name: str,
    required_skills: tuple[str, ...],
    all_installed_vars: dict[str, str],
    none_installed_vars: dict[str, str],
) -> None:
    installed = _render_phase(template_name, all_installed_vars)
    absent = _render_phase(template_name, none_installed_vars)

    for skill in required_skills:
        assert skill in installed
    for skill in set(FROZEN_UNIVERSAL_SKILLS) - set(required_skills):
        assert skill not in installed
    for skill in FROZEN_UNIVERSAL_SKILLS:
        assert skill not in absent
    for hint in _PHASE_FALLBACK_HINTS.get(template_name, ()):
        assert hint in absent


@pytest.mark.parametrize(
    ("template_name", "skill_name"),
    [
        (template_name, skill_name)
        for template_name, required_skills in _PHASE_REQUIRED_SKILLS.items()
        for skill_name in required_skills
    ],
)
def test_each_phase_skill_cue_uses_its_own_installed_flag(
    template_name: str,
    skill_name: str,
) -> None:
    installed = _render_phase(
        template_name,
        skill_template_variables((skill_name,)),
    )
    absent = _render_phase(
        template_name,
        skill_template_variables(()),
    )

    assert skill_name in installed
    assert skill_name not in absent
    for other_skill in set(FROZEN_UNIVERSAL_SKILLS) - {skill_name}:
        assert other_skill not in installed


def test_templates_name_only_known_universal_and_artifact_skills() -> None:
    allowed_names = set(FROZEN_UNIVERSAL_SKILLS) | set(_ARTIFACT_SKILL_NAMES)

    for template_name, source in _template_sources().items():
        cue_names = {match.group(1) for match in _CUE_NAME_RE.finditer(source)}
        artifact_names = {match.group(1) for match in _ARTIFACT_POINTER_RE.finditer(source)}
        universal_names = {
            match.group(1).lower().replace("_", "-")
            for match in _UNIVERSAL_VARIABLE_RE.finditer(source)
        }
        assert cue_names <= allowed_names, template_name
        assert artifact_names <= _ARTIFACT_SKILL_NAMES, template_name
        assert universal_names <= set(FROZEN_UNIVERSAL_SKILLS), template_name


def test_skill_cue_text_uses_no_emphatic_all_caps_words() -> None:
    for template_name, source in _template_sources().items():
        for line in source.splitlines():
            names_skill = "SKILL_INSTALLED_" in line or any(
                skill in line for skill in FROZEN_UNIVERSAL_SKILLS
            )
            if names_skill:
                assert not _EMPHATIC_RE.search(line), template_name
