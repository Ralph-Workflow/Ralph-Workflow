"""Regression tests for file-backed prompt template assets."""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.tools.artifact import handle_submit_artifact
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import render_worker_prompt
from tests.test_prompt_template_files_helper__workspace import _Workspace

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_ROOT = REPO_ROOT / "ralph" / "prompts"
TEMPLATES_ROOT = PROMPTS_ROOT / "templates"
SHARED_ROOT = TEMPLATES_ROOT / "shared"



class _ApprovedSession:
    session_id = "session-1"

    def __init__(self, *, drain: str = "development_analysis") -> None:
        self.drain = drain

    def check_capability(self, capability: str) -> object:
        assert capability == "artifact.submit"
        return "approved"




def test_legacy_prompt_families_have_file_backed_jinja_templates() -> None:
    expected_templates = {
        "commit_message.jinja",
        "commit_simplified.jinja",
        "conflict_resolution.jinja",
        "conflict_resolution_fallback.jinja",
        "developer_iteration.jinja",
        "developer_iteration_continuation.jinja",
        "development_analysis.jinja",
        "fix_mode.jinja",
        "planning.jinja",
        "planning_edit.jinja",
        "planning_analysis.jinja",
        "review.jinja",
        "review_analysis.jinja",
    }

    actual_templates = {path.name for path in TEMPLATES_ROOT.glob("*.jinja")}

    assert expected_templates <= actual_templates


def test_legacy_shared_partials_have_one_jinja_file_each() -> None:
    expected_partials = {
        "_analysis_context.jinja",
        "_context_section.jinja",
        "_critical_header.jinja",
        "_developer_iteration_guidance.jinja",
        "_diff_section.jinja",
        "_mcp_tools.jinja",
        "_no_git_commit.jinja",
        "_output_checklist.jinja",
        "_safety_no_execute.jinja",
        "_session_capabilities.jinja",
        "_unattended_mode.jinja",
    }

    actual_partials = {path.name for path in SHARED_ROOT.glob("*.jinja")}

    assert expected_partials <= actual_partials


def test_default_artifacts_policy_references_file_backed_templates() -> None:
    artifacts_toml = (REPO_ROOT / "ralph" / "policy" / "defaults" / "artifacts.toml").read_text(
        encoding="utf-8"
    )

    assert 'prompt_template = "planning.jinja"' in artifacts_toml
    assert 'prompt_template = "planning_analysis.jinja"' in artifacts_toml
    assert 'prompt_template = "development_analysis.jinja"' in artifacts_toml
    assert 'prompt_template = "commit_message.jinja"' in artifacts_toml
    assert 'prompt_template = ""' not in artifacts_toml


def test_rendered_prompts_are_dumped_under_agent_tmp() -> None:
    prompt_debug_module = PROMPTS_ROOT / "debug_dump.py"

    assert prompt_debug_module.exists()


def test_all_top_level_templates_include_unattended_partial() -> None:
    template_files = [path for path in TEMPLATES_ROOT.glob("*.jinja") if path.is_file()]

    missing = [
        path.name
        for path in template_files
        if "_unattended_mode" not in path.read_text(encoding="utf-8")
    ]

    assert missing == []


ANALYSIS_EXHAUSTIVE_FAILURE_GUIDANCE = (
    "**List every gap found** across all dimensions. Do not stop after the first problem."
)
ANALYSIS_OMISSION_GUIDANCE = (
    "3. **Cite concrete evidence.** File paths, function names, test names, command output,"
)
ANALYSIS_NO_FIRST_PROBLEM_GUIDANCE = "Do not stop after the first problem."
ANALYSIS_EVALUATABLE_ONLY_GUIDANCE = "## ONLY JUDGE WHAT YOU CAN EVALUATE"
ANALYSIS_NO_ASSUMED_HUMAN_BEHAVIOR_GUIDANCE = (
    "You MUST NOT judge what the agent, developer, or reviewer did or did not do."
)
ANALYSIS_RUN_COMMAND_OR_NO_COMMAND_GUIDANCE = "no runnable verification command exists"
ANALYSIS_NO_PLUMBING_OVERWEIGHT_GUIDANCE = (
    "Do not fail a plan solely because of Ralph Workflow internal exec or transport quirks"
)
ANALYSIS_NO_TRANSIENT_HANDOFF_FAILURE_GUIDANCE = (
    "Do not treat transient .agent handoff plumbing as a blocking repository-accuracy defect"
)
ANALYSIS_NO_AGENT_LOG_GUIDANCE = (
    "You MUST NOT rely on agent logs, transcripts, or inferred process history "
    "as evaluation evidence."
)
ANALYSIS_CODE_DIFF_ONLY_GUIDANCE = "Evaluation evidence may come ONLY from: (1) code and diff"
ANALYSIS_CONTEXT_ONLY_ARTIFACT_GUIDANCE = "context only"
ANALYSIS_VERIFY_ARTIFACT_CLAIMS_GUIDANCE = (
    "If an artifact makes a claim, verify that claim against the code, diff, or command output"
)
ANALYSIS_PLAN_NOT_EVIDENCE_GUIDANCE = (
    "The PLAN defines the goal you are evaluating against; it is not evidence by itself."
)
ANALYSIS_EVIDENCE_AGAINST_PLAN_GUIDANCE = "available empirical evidence"
ANALYSIS_NOT_EVALUATABLE_GUIDANCE = "not evaluatable"
ANALYSIS_SKIP_UNEVALUATABLE_GUIDANCE = "skip it as a scored judgment"
ANALYSIS_NO_NEED_ENUMERATE_SKIPS_GUIDANCE = "skipped item in the artifact."
ANALYSIS_EVALUATABLE_DIMENSIONS_ONLY_GUIDANCE = "that is evaluatable from"
DEVELOPMENT_ANALYSIS_FRESH_SUBMIT_EXAMPLE = (
    '"artifact_type":"development_analysis_decision",'
    '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"'
)
REVIEW_ANALYSIS_FRESH_SUBMIT_EXAMPLE = (
    '"artifact_type":"review_analysis_decision",'
    '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"'
)
PLANNING_ANALYSIS_FRESH_SUBMIT_EXAMPLE = (
    '"artifact_type":"planning_analysis_decision",'
    '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"'
)
PLANNING_DISCOVERY_PREFLIGHT_GUIDANCE = (
    "Before you commit to a file path, pattern, dependency, or verification "
    "command, inspect it yourself"
)
PLANNING_NO_VAGUE_PATTERN_GUIDANCE = (
    'Do not use phrases like "follow the existing pattern" unless you name the exact reference file'
)
PLANNING_LOW_RESEARCH_EXECUTOR_GUIDANCE = (
    "If the executor would need to stop and research basic repository structure"
)
PLANNING_FIRST_PASS_APPROVAL_GUIDANCE = (
    "Your target is to submit a plan that planning analysis can approve on the first pass"
)
PLANNING_SELF_CRITIQUE_GUIDANCE = (
    "Before finalizing, use parallel agents for the planning-analysis pass when possible; "
    "otherwise do the next best thing and simulate it yourself"
)
PLANNING_CORE_WORKFLOW_PLANNING_GUIDANCE = (
    "Infer the core user-facing workflows and prerequisite actions that must exist"
)
PLANNING_ANALYSIS_CRITIC_GUIDANCE = "You are a lightweight plan critic"
PLANNING_ANALYSIS_MISSING_WORK_GUIDANCE = "Missing work"
PLANNING_ANALYSIS_CONTRADICTIONS_GUIDANCE = "Contradictions or inconsistency"
PLANNING_ANALYSIS_RESEARCH_BURDEN_GUIDANCE = "Executor research burden"
PLANNING_EDIT_GET_DRAFT_GUIDANCE = (
    "Use `{{GET_PLAN_DRAFT_TOOL_NAME}}` to inspect the current finalized plan "
    "or staged draft before editing."
)
PLANNING_EDIT_SECTION_REPLACE_GUIDANCE = (
    "Use `{{SUBMIT_PLAN_SECTION_TOOL_NAME}}` to replace only the sections that need revision."
)
PLANNING_EDIT_FINALIZE_GUIDANCE = (
    "Use `{{FINALIZE_PLAN_TOOL_NAME}}` after revising the affected sections so "
    "the updated plan replaces the prior finalized plan."
)
PLANNING_EDIT_DEFECT_SCOPE_GUIDANCE = "Before revising any section, classify the feedback scope"
PLANNING_EDIT_GLOBAL_REDERIVATION_GUIDANCE = (
    "If any feedback item reveals repo-wide incompleteness, invalid inventory, incorrect paths, "
    "narrow verification, or prompt-to-plan traceability gaps, you MUST re-derive the plan"
)
PLANNING_EDIT_SELF_AUDIT_GUIDANCE = "Before `{{FINALIZE_PLAN_TOOL_NAME}}`, perform this self-audit"
PLANNING_EDIT_RISK_COVERAGE_GUIDANCE = (
    "- Risk coverage: concrete risks, mitigations, and edge cases are represented"
)
PLANNING_EDIT_PARALLELIZATION_GUIDANCE = (
    "- Parallelization safety: any parallel work remains disjoint, realistic, and policy-compliant"
)
PLANNING_EDIT_MAINTAINABILITY_GUIDANCE = (
    "- Maintainability and handoff quality: the plan stays concise, "
    "non-redundant, and explicit for development handoff"
)
PLANNING_EDIT_SCOPE_INVALIDATION_GUIDANCE = (
    "If the ORIGINAL REQUEST has repository-wide acceptance criteria and the current plan "
    "narrowed scope before running repository-wide discovery"
)
PLANNING_EDIT_DISCOVERY_FIRST_GUIDANCE = (
    "replace the summary, scope, and early steps so Step 1 becomes repo-wide discovery"
)
PLANNING_EDIT_SCOPE_DERIVATION_GUIDANCE = (
    "- Scope derivation: when the task is repo-wide, implementation scope comes from an "
    "explicit repo-wide discovery step rather than a guessed subsystem"
)
PLANNING_EDIT_PASS_TARGET_GUIDANCE = (
    "Your target is to submit the strongest revised plan you can so the next planning-analysis pass"
)
PLANNING_EDIT_NO_KNOWN_GAPS_GUIDANCE = (
    "Do not finalize a draft that still has any known unresolved analyzer finding"
)
PLANNING_EDIT_DEPENDENT_SECTION_REWRITE_GUIDANCE = (
    "If fixing one section changes the truth of another section, replace every dependent section"
)
PLANNING_EDIT_NEXT_ANALYZER_GUIDANCE = (
    "Before finalizing, proactively search for any additional repo-grounded failure"
)
PLANNING_EDIT_SURFACED_BLOCKER_GUIDANCE = (
    "If a canonical verification command or repo-wide audit already surfaces a blocker "
    "during replanning"
)
PLANNING_EDIT_RULE_CATEGORY_GUIDANCE = (
    "When the ORIGINAL REQUEST imposes repo-wide structural rules, build a repo-wide inventory"
)
PLANNING_EDIT_NO_EXCEPTION_GUIDANCE = (
    "Do not preserve prompt-violating tests, files, or workflows as justified exceptions"
)
PLANNING_EDIT_STARTING_POINT_GUIDANCE = (
    "Treat the planning-analysis feedback as a starting point, not as the full list of issues"
)
PLANNING_EDIT_NOT_LOCAL_PATCH_GUIDANCE = (
    "Do not localize your revision pass to only the sections explicitly cited by the analyzer"
)
PLANNING_EDIT_SELF_ANALYSIS_GUIDANCE = (
    "You must perform your own repo-grounded analysis before finalizing"
)
PLANNING_EDIT_ISSUE_MAPPING_GUIDANCE = (
    "Every analyzer issue must map to concrete revised sections or an explicit verified reason"
)
PLANNING_ANALYSIS_DEFECT_SCOPE_GUIDANCE = "first classify the overall defect scope"
PLANNING_ANALYSIS_VISIBLE_GAPS_GUIDANCE = "enumerate all currently visible repo-grounded gaps"
PLANNING_ANALYSIS_MCP_REMEDIATION_GUIDANCE = (
    "When describing remediation, target the planner's MCP revision workflow"
)
PLANNING_ANALYSIS_SECTION_RESUBMIT_GUIDANCE = (
    "Exact plan sections to resubmit via the MCP plan-edit tools."
)
PLANNING_EXECUTION_PLAN_GUIDANCE = "Create a detailed, structured execution plan."
PLANNING_EDIT_EXECUTION_PLAN_GUIDANCE = (
    "Revise the existing execution plan in response to planning-analysis feedback."
)
PLANNING_ANALYSIS_NO_DOWNSTREAM_REPLANNING_GUIDANCE = (
    "if the plan asks the executor to perform planning work mid-flight"
)
PLANNING_ANALYSIS_CORE_WORKFLOW_GUIDANCE = (
    "Infer the core user-facing workflows and prerequisite actions that must exist"
)
PLANNING_ANALYSIS_PREREQUISITE_CHAIN_GUIDANCE = (
    "Goal → required workflow → prerequisite action/state → plan coverage"
)
PLANNING_EDIT_CLOSURE_LEDGER_GUIDANCE = (
    "Build a closure ledger before finalizing the revised draft"
)
PLANNING_EDIT_ADJACENT_ISSUES_GUIDANCE = (
    "the adjacent or implied issues your own analysis discovered"
)
PLANNING_SHARED_DEFECT_VOCAB_GUIDANCE = (
    "Use this shared defect vocabulary in both analysis findings and replanning fixes"
)
PLANNING_DEPENDENT_SECTION_CLOSURE_GUIDANCE = (
    "any dependent sections that must also change for the plan to become coherent"
)
PLANNING_STABLE_ID_GUIDANCE = (
    "Assign a stable ID to every gap you report and preserve that ID in the corresponding fix entry"
)
PLANNING_PARALLEL_ANALYSIS_GUIDANCE = (
    "If multiple independent analysis or discovery threads would reduce uncertainty, "
    "use parallel agents"
)
PLANNING_FIRST_PASS_RISK_AUDIT_GUIDANCE = (
    "Before finalizing, verify the draft also covers concrete risks, safe parallelization, "
    "and handoff quality"
)
PLANNING_ANALYSIS_FORMAT_GUIDANCE = (
    "Use this exact string format for each `what_came_up_short` entry"
)
PLANNING_EDIT_FALLBACK_HISTORY_GUIDANCE = (
    "Inspect this history to understand what plans have been tried and rejected before"
)
PLANNING_EDIT_FALLBACK_SCOPE_CONDITIONAL_GUIDANCE = (
    "If the defect scope is `repo_wide`, replace the summary, scope, and early steps"
)
DEVELOPER_SMALL_CHANGE_GUIDANCE = (
    "Make the smallest self-contained change that solves the problem"
)
DEVELOPER_NATIVE_CHECKS_GUIDANCE = (
    "Detect and use the repository's native formatter, linter, type checker, test runner, "
    "and build checks"
)
DEVELOPER_NO_BYPASS_GUIDANCE = (
    "Never weaken quality gates to get green: do not disable tests, bypass lint rules"
)
DEVELOPER_LOCAL_SUPPRESSION_GUIDANCE = (
    "If a suppression is truly unavoidable, keep it local to the narrowest scope"
)
DEVELOPER_TESTABLE_DESIGN_GUIDANCE = (
    "Prefer testable design: use dependency injection, explicit seams, pure logic"
)
DEVELOPER_REFACTOR_FOR_TESTABILITY_GUIDANCE = (
    "If tests are hard to write or require real external I/O for routine coverage"
)
DEVELOPER_SAME_CHANGE_TESTS_GUIDANCE = (
    "For behavior changes and bug fixes, add or update tests in the same change"
)
DEVELOPER_VERIFICATION_EVIDENCE_GUIDANCE = (
    "Do not claim completion until the relevant verification commands actually pass"
)
DEVELOPER_ADD_QUALITY_STACK_GUIDANCE = (
    "A finished change should leave behind a quality gate you would trust"
)
DEVELOPER_STRONG_QUALITY_BASELINE_GUIDANCE = (
    "establish the ecosystem's best-practice baseline yourself"
)
DEVELOPER_BYPASS_GUARD_GUIDANCE = (
    "Make casual bypasses visible in that gate"
)
DEVELOPER_CLEAR_OVER_CLEVER_GUIDANCE = (
    "Prefer straightforward, maintainable code over clever tricks or premature abstraction"
)
DEVELOPER_NARROW_INTERFACES_GUIDANCE = (
    "Keep interfaces narrow and explicit so behavior stays easy to understand"
)
DEVELOPER_DEPENDENCY_DISCIPLINE_GUIDANCE = (
    "Add dependencies, abstractions, or layers only when they clearly reduce complexity"
)


def _assert_shared_analysis_guidance(
    development_analysis: str,
    review_analysis: str,
) -> None:
    assert ANALYSIS_EXHAUSTIVE_FAILURE_GUIDANCE in development_analysis
    assert ANALYSIS_EXHAUSTIVE_FAILURE_GUIDANCE in review_analysis
    assert ANALYSIS_OMISSION_GUIDANCE in development_analysis
    assert ANALYSIS_OMISSION_GUIDANCE in review_analysis
    assert ANALYSIS_NO_FIRST_PROBLEM_GUIDANCE in development_analysis
    assert ANALYSIS_NO_FIRST_PROBLEM_GUIDANCE in review_analysis
    assert ANALYSIS_EVALUATABLE_ONLY_GUIDANCE in development_analysis
    assert ANALYSIS_EVALUATABLE_ONLY_GUIDANCE in review_analysis
    assert ANALYSIS_NO_ASSUMED_HUMAN_BEHAVIOR_GUIDANCE in development_analysis
    assert ANALYSIS_NO_ASSUMED_HUMAN_BEHAVIOR_GUIDANCE in review_analysis
    assert ANALYSIS_RUN_COMMAND_OR_NO_COMMAND_GUIDANCE in development_analysis
    assert ANALYSIS_RUN_COMMAND_OR_NO_COMMAND_GUIDANCE in review_analysis
    assert ANALYSIS_NO_AGENT_LOG_GUIDANCE in development_analysis
    assert ANALYSIS_NO_AGENT_LOG_GUIDANCE in review_analysis
    assert ANALYSIS_CODE_DIFF_ONLY_GUIDANCE in development_analysis
    assert ANALYSIS_CODE_DIFF_ONLY_GUIDANCE in review_analysis
    assert ANALYSIS_CONTEXT_ONLY_ARTIFACT_GUIDANCE in development_analysis
    assert ANALYSIS_CONTEXT_ONLY_ARTIFACT_GUIDANCE in review_analysis
    assert ANALYSIS_VERIFY_ARTIFACT_CLAIMS_GUIDANCE in development_analysis
    assert ANALYSIS_VERIFY_ARTIFACT_CLAIMS_GUIDANCE in review_analysis
    assert ANALYSIS_PLAN_NOT_EVIDENCE_GUIDANCE in development_analysis
    assert ANALYSIS_PLAN_NOT_EVIDENCE_GUIDANCE in review_analysis
    assert ANALYSIS_EVIDENCE_AGAINST_PLAN_GUIDANCE in development_analysis
    assert ANALYSIS_EVIDENCE_AGAINST_PLAN_GUIDANCE in review_analysis
    assert ANALYSIS_NOT_EVALUATABLE_GUIDANCE in development_analysis
    assert ANALYSIS_NOT_EVALUATABLE_GUIDANCE in review_analysis
    assert ANALYSIS_SKIP_UNEVALUATABLE_GUIDANCE in development_analysis
    assert ANALYSIS_SKIP_UNEVALUATABLE_GUIDANCE in review_analysis
    assert ANALYSIS_NO_NEED_ENUMERATE_SKIPS_GUIDANCE in development_analysis
    assert ANALYSIS_NO_NEED_ENUMERATE_SKIPS_GUIDANCE in review_analysis
    assert ANALYSIS_EVALUATABLE_DIMENSIONS_ONLY_GUIDANCE in review_analysis


def test_analysis_templates_require_exact_artifact_types_and_detailed_fix_sections() -> None:
    development_analysis = (TEMPLATES_ROOT / "development_analysis.jinja").read_text(
        encoding="utf-8"
    )
    planning_analysis = (TEMPLATES_ROOT / "planning_analysis.jinja").read_text(encoding="utf-8")
    review_analysis = (TEMPLATES_ROOT / "review_analysis.jinja").read_text(encoding="utf-8")

    assert 'artifact_type="development_analysis_decision"' in development_analysis
    assert 'artifact_type="planning_analysis_decision"' in planning_analysis
    assert 'artifact_type="review_analysis_decision"' in review_analysis
    assert "what_came_up_short" in development_analysis
    assert "how_to_fix" in development_analysis
    assert "what_came_up_short" in planning_analysis
    assert "how_to_fix" in planning_analysis
    assert "what_came_up_short" in review_analysis
    assert "how_to_fix" in review_analysis
    assert "Not submitting the analysis artifact is a FAILURE." in development_analysis
    assert "Not submitting the analysis artifact is a FAILURE." in planning_analysis
    assert "Not submitting the analysis artifact is a FAILURE." in review_analysis
    assert "SUBMIT_ARTIFACT_TOOL_REFERENCE" in development_analysis
    assert "SUBMIT_ARTIFACT_TOOL_REFERENCE" in planning_analysis
    assert "SUBMIT_ARTIFACT_TOOL_REFERENCE" in review_analysis
    assert "approved" not in planning_analysis
    assert "approved" not in review_analysis
    assert "reject" not in planning_analysis
    assert "reject" not in review_analysis
    assert "Use `content` for a freshly generated JSON string." in development_analysis
    assert "Use `content` for a freshly generated JSON string." in planning_analysis
    assert "Use `content` for a freshly generated JSON string." in review_analysis
    assert "content_path" not in development_analysis
    assert "content_path" not in planning_analysis
    assert "content_path" not in review_analysis
    assert DEVELOPMENT_ANALYSIS_FRESH_SUBMIT_EXAMPLE in development_analysis
    assert PLANNING_ANALYSIS_FRESH_SUBMIT_EXAMPLE in planning_analysis
    assert REVIEW_ANALYSIS_FRESH_SUBMIT_EXAMPLE in review_analysis
    _assert_shared_analysis_guidance(development_analysis, review_analysis)
    _assert_shared_analysis_guidance(planning_analysis, review_analysis)


def test_planning_fallback_templates_reference_artifact_history_location() -> None:
    planning_fallback = (TEMPLATES_ROOT / "planning_fallback.jinja").read_text(encoding="utf-8")
    planning_edit_fallback = (TEMPLATES_ROOT / "planning_edit_fallback.jinja").read_text(
        encoding="utf-8"
    )

    assert "ARTIFACT_HISTORY_PATH" in planning_fallback
    assert "ARTIFACT_HISTORY_DIR" in planning_fallback
    assert "ARTIFACT_HISTORY_PATH" in planning_edit_fallback
    assert "ARTIFACT_HISTORY_DIR" in planning_edit_fallback


def test_planning_prompt_requires_verified_low_research_executor_handoff() -> None:
    planning = (TEMPLATES_ROOT / "planning.jinja").read_text(encoding="utf-8")

    assert PLANNING_DISCOVERY_PREFLIGHT_GUIDANCE in planning
    assert PLANNING_NO_VAGUE_PATTERN_GUIDANCE in planning
    assert PLANNING_LOW_RESEARCH_EXECUTOR_GUIDANCE in planning
    assert PLANNING_FIRST_PASS_APPROVAL_GUIDANCE in planning
    assert PLANNING_SELF_CRITIQUE_GUIDANCE in planning
    assert PLANNING_CORE_WORKFLOW_PLANNING_GUIDANCE in planning
    assert PLANNING_SHARED_DEFECT_VOCAB_GUIDANCE in planning
    assert PLANNING_DEPENDENT_SECTION_CLOSURE_GUIDANCE in planning
    assert PLANNING_STABLE_ID_GUIDANCE in planning
    assert PLANNING_PARALLEL_ANALYSIS_GUIDANCE in planning
    assert PLANNING_FIRST_PASS_RISK_AUDIT_GUIDANCE in planning


def test_planning_edit_prompt_teaches_repo_wide_recomputation_not_just_local_patching() -> None:
    planning_edit = (TEMPLATES_ROOT / "planning_edit.jinja").read_text(encoding="utf-8")

    assert "PLANNING EDIT MODE" in planning_edit
    assert "The prior plan was rejected by planning analysis." in planning_edit
    assert PLANNING_EDIT_GET_DRAFT_GUIDANCE in planning_edit
    assert PLANNING_EDIT_FINALIZE_GUIDANCE in planning_edit
    assert PLANNING_EDIT_DEFECT_SCOPE_GUIDANCE in planning_edit
    assert PLANNING_EDIT_GLOBAL_REDERIVATION_GUIDANCE in planning_edit
    assert PLANNING_EDIT_SELF_AUDIT_GUIDANCE in planning_edit
    assert PLANNING_EDIT_RISK_COVERAGE_GUIDANCE in planning_edit
    assert PLANNING_EDIT_PARALLELIZATION_GUIDANCE in planning_edit
    assert PLANNING_EDIT_MAINTAINABILITY_GUIDANCE in planning_edit
    assert PLANNING_EDIT_SCOPE_INVALIDATION_GUIDANCE in planning_edit
    assert PLANNING_EDIT_DISCOVERY_FIRST_GUIDANCE in planning_edit
    assert PLANNING_EDIT_SCOPE_DERIVATION_GUIDANCE in planning_edit
    assert PLANNING_EDIT_PASS_TARGET_GUIDANCE in planning_edit
    assert PLANNING_EDIT_NO_KNOWN_GAPS_GUIDANCE in planning_edit
    assert PLANNING_EDIT_DEPENDENT_SECTION_REWRITE_GUIDANCE in planning_edit
    assert PLANNING_EDIT_NEXT_ANALYZER_GUIDANCE in planning_edit
    assert PLANNING_EDIT_SURFACED_BLOCKER_GUIDANCE in planning_edit
    assert PLANNING_EDIT_RULE_CATEGORY_GUIDANCE in planning_edit
    assert PLANNING_EDIT_NO_EXCEPTION_GUIDANCE in planning_edit
    assert PLANNING_EDIT_STARTING_POINT_GUIDANCE in planning_edit
    assert PLANNING_EDIT_NOT_LOCAL_PATCH_GUIDANCE in planning_edit
    assert PLANNING_EDIT_SELF_ANALYSIS_GUIDANCE in planning_edit
    assert PLANNING_EDIT_ISSUE_MAPPING_GUIDANCE in planning_edit
    assert PLANNING_EDIT_CLOSURE_LEDGER_GUIDANCE in planning_edit
    assert PLANNING_EDIT_ADJACENT_ISSUES_GUIDANCE in planning_edit
    assert PLANNING_SHARED_DEFECT_VOCAB_GUIDANCE in planning_edit
    assert PLANNING_DEPENDENT_SECTION_CLOSURE_GUIDANCE in planning_edit
    assert PLANNING_STABLE_ID_GUIDANCE in planning_edit
    assert PLANNING_PARALLEL_ANALYSIS_GUIDANCE in planning_edit
    assert 'artifact_type="plan"' not in planning_edit
    assert "Not submitting the revised plan is a FAILURE." in planning_edit


def test_planning_analysis_prompt_demands_gap_and_consistency_critique() -> None:
    planning_analysis = (TEMPLATES_ROOT / "planning_analysis.jinja").read_text(encoding="utf-8")

    assert PLANNING_ANALYSIS_CRITIC_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_MISSING_WORK_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_CONTRADICTIONS_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_RESEARCH_BURDEN_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_DEFECT_SCOPE_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_VISIBLE_GAPS_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_MCP_REMEDIATION_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_SECTION_RESUBMIT_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_CORE_WORKFLOW_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_PREREQUISITE_CHAIN_GUIDANCE in planning_analysis
    assert PLANNING_SHARED_DEFECT_VOCAB_GUIDANCE in planning_analysis
    assert PLANNING_DEPENDENT_SECTION_CLOSURE_GUIDANCE in planning_analysis
    assert PLANNING_STABLE_ID_GUIDANCE in planning_analysis
    assert PLANNING_PARALLEL_ANALYSIS_GUIDANCE in planning_analysis
    assert PLANNING_ANALYSIS_FORMAT_GUIDANCE in planning_analysis


def test_planning_edit_fallback_stays_aligned_with_main_replanning_contract() -> None:
    planning_edit_fallback = (TEMPLATES_ROOT / "planning_edit_fallback.jinja").read_text(
        encoding="utf-8"
    )

    assert PLANNING_EDIT_CLOSURE_LEDGER_GUIDANCE in planning_edit_fallback
    assert PLANNING_EDIT_ADJACENT_ISSUES_GUIDANCE in planning_edit_fallback
    assert PLANNING_SHARED_DEFECT_VOCAB_GUIDANCE in planning_edit_fallback
    assert PLANNING_ANALYSIS_CORE_WORKFLOW_GUIDANCE in planning_edit_fallback
    assert PLANNING_DEPENDENT_SECTION_CLOSURE_GUIDANCE in planning_edit_fallback
    assert PLANNING_STABLE_ID_GUIDANCE in planning_edit_fallback
    assert PLANNING_PARALLEL_ANALYSIS_GUIDANCE in planning_edit_fallback
    assert PLANNING_EDIT_FALLBACK_HISTORY_GUIDANCE in planning_edit_fallback
    assert PLANNING_EDIT_FALLBACK_SCOPE_CONDITIONAL_GUIDANCE in planning_edit_fallback


def test_fix_and_developer_iteration_templates_use_analysis_context_partial() -> None:
    fix_template = (TEMPLATES_ROOT / "fix_mode.jinja").read_text(encoding="utf-8")
    dev_template = (TEMPLATES_ROOT / "developer_iteration.jinja").read_text(encoding="utf-8")

    assert "shared/_analysis_context" in fix_template
    assert "shared/_analysis_context" in dev_template
    assert "render_payload_section('ISSUES'" not in fix_template
    assert "render_payload_section('ANALYSIS FEEDBACK'" not in fix_template
    assert "render_payload_section('ANALYSIS FEEDBACK'" not in dev_template


def test_developer_iteration_templates_include_universal_quality_guidance() -> None:
    shared_guidance = (TEMPLATES_ROOT / "shared" / "_developer_iteration_guidance.jinja").read_text(
        encoding="utf-8"
    )
    dev_template = (TEMPLATES_ROOT / "developer_iteration.jinja").read_text(encoding="utf-8")
    continuation_template = (TEMPLATES_ROOT / "developer_iteration_continuation.jinja").read_text(
        encoding="utf-8"
    )
    fallback_template = (TEMPLATES_ROOT / "developer_iteration_fallback.jinja").read_text(
        encoding="utf-8"
    )

    assert DEVELOPER_SMALL_CHANGE_GUIDANCE in shared_guidance
    assert DEVELOPER_NATIVE_CHECKS_GUIDANCE in shared_guidance
    assert DEVELOPER_NO_BYPASS_GUIDANCE in shared_guidance
    assert DEVELOPER_LOCAL_SUPPRESSION_GUIDANCE in shared_guidance
    assert DEVELOPER_TESTABLE_DESIGN_GUIDANCE in shared_guidance
    assert DEVELOPER_REFACTOR_FOR_TESTABILITY_GUIDANCE in shared_guidance
    assert DEVELOPER_SAME_CHANGE_TESTS_GUIDANCE in shared_guidance
    assert DEVELOPER_VERIFICATION_EVIDENCE_GUIDANCE in shared_guidance
    assert DEVELOPER_ADD_QUALITY_STACK_GUIDANCE in shared_guidance
    assert DEVELOPER_STRONG_QUALITY_BASELINE_GUIDANCE in shared_guidance
    assert DEVELOPER_BYPASS_GUARD_GUIDANCE in shared_guidance
    assert DEVELOPER_CLEAR_OVER_CLEVER_GUIDANCE in shared_guidance
    assert DEVELOPER_NARROW_INTERFACES_GUIDANCE in shared_guidance
    assert DEVELOPER_DEPENDENCY_DISCIPLINE_GUIDANCE in shared_guidance
    assert "shared/_developer_iteration_guidance" in dev_template
    assert "shared/_developer_iteration_guidance" in continuation_template
    assert "shared/_developer_iteration_guidance" in fallback_template


def test_review_and_fix_templates_define_explicit_review_handoff_contracts() -> None:
    review_template = (TEMPLATES_ROOT / "review.jinja").read_text(encoding="utf-8")
    fix_template = (TEMPLATES_ROOT / "fix_mode.jinja").read_text(encoding="utf-8")

    assert 'artifact_type="issues"' in review_template
    assert "what_came_up_short" in review_template
    assert "how_to_fix" in review_template
    assert "FIX_RESULT" in review_template
    assert "fix_result" not in fix_template
    assert "submit" not in fix_template.lower()


def test_development_analysis_prompt_taught_variants_submit_successfully(tmp_path: Path) -> None:
    session = _ApprovedSession(drain="development_analysis")
    workspace = _Workspace(tmp_path)
    payloads = [
        {"status": "completed", "summary": "Implementation looks correct."},
        {
            "status": "request_changes",
            "summary": "Implementation needs another pass.",
            "what_came_up_short": ["Required verification evidence is missing."],
            "how_to_fix": ["Add the missing verification evidence and rerun checks."],
        },
        {
            "status": "failed",
            "summary": "The analysis could not complete.",
            "what_came_up_short": ["The required evidence was unavailable."],
            "how_to_fix": ["Reproduce the evidence and rerun the analysis."],
        },
    ]

    for index, payload in enumerate(payloads):
        result = handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "development_analysis_decision",
                "content": json.dumps(payload),
            },
        )
        assert result.is_error is False, f"payload #{index} should submit successfully"


def test_review_analysis_prompt_taught_variants_submit_successfully(tmp_path: Path) -> None:
    session = _ApprovedSession(drain="review_analysis")
    workspace = _Workspace(tmp_path)
    payloads = [
        {"status": "completed", "summary": "Review looks good."},
        {
            "status": "request_changes",
            "summary": "Fixes are required.",
            "what_came_up_short": ["A regression test is still missing."],
            "how_to_fix": ["Add the regression test and rerun review verification."],
        },
        {
            "status": "failed",
            "summary": "The review analysis could not complete.",
            "what_came_up_short": ["The review evidence was incomplete."],
            "how_to_fix": ["Regenerate the evidence and rerun review analysis."],
        },
    ]

    for index, payload in enumerate(payloads):
        result = handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "review_analysis_decision",
                "content": json.dumps(payload),
            },
        )
        assert result.is_error is False, f"payload #{index} should submit successfully"


def test_planning_analysis_prompt_taught_variants_submit_successfully(tmp_path: Path) -> None:
    session = _ApprovedSession(drain="planning_analysis")
    workspace = _Workspace(tmp_path)
    payloads = [
        {"status": "completed", "summary": "The plan is executor-ready."},
        {
            "status": "request_changes",
            "summary": "The plan needs another pass.",
            "what_came_up_short": [
                "Critical files do not identify the real execution touchpoints."
            ],
            "how_to_fix": ["Update critical_files and add exact verification commands."],
        },
        {
            "status": "failed",
            "summary": "The planning analysis could not approve this plan.",
            "what_came_up_short": ["The plan is missing executable implementation steps."],
            "how_to_fix": [
                "Rewrite the steps so a weaker unattended agent can execute them literally."
            ],
        },
    ]

    for index, payload in enumerate(payloads):
        result = handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "planning_analysis_decision",
                "content": json.dumps(payload),
            },
        )
        assert result.is_error is False, f"payload #{index} should submit successfully"


def test_commit_prompt_taught_variants_submit_successfully(tmp_path: Path) -> None:
    session = _ApprovedSession(drain="development_commit")
    workspace = _Workspace(tmp_path)
    payloads = [
        {"type": "commit", "subject": "fix(parser): preserve prefixes"},
        {"type": "skip", "reason": "No task-related changes to commit."},
    ]

    for index, payload in enumerate(payloads):
        result = handle_submit_artifact(
            session,
            workspace,
            {
                "artifact_type": "commit_message",
                "content": json.dumps(payload),
            },
        )
        assert result.is_error is False, f"payload #{index} should submit successfully"


def test_analysis_templates_define_failed_as_stronger_major_remediation() -> None:
    development_analysis = (TEMPLATES_ROOT / "development_analysis.jinja").read_text(
        encoding="utf-8"
    )
    planning_analysis = (TEMPLATES_ROOT / "planning_analysis.jinja").read_text(encoding="utf-8")
    review_analysis = (TEMPLATES_ROOT / "review_analysis.jinja").read_text(encoding="utf-8")

    assert "major incompleteness" in development_analysis.lower()
    assert "major incompleteness" in planning_analysis.lower()
    assert "major incompleteness" in review_analysis.lower()
    assert "start over" in development_analysis.lower() or "redo" in development_analysis.lower()
    assert "start over" in planning_analysis.lower() or "redo" in planning_analysis.lower()
    assert "start over" in review_analysis.lower() or "redo" in review_analysis.lower()
    assert "security vulnerability, or data loss risk" not in development_analysis
    assert "security vulnerability, or data loss risk" not in planning_analysis
    assert "fundamentally incomplete or missed critical issues" not in review_analysis


def test_planning_analysis_template_deemphasizes_internal_plumbing_failures() -> None:
    planning_analysis = (TEMPLATES_ROOT / "planning_analysis.jinja").read_text(encoding="utf-8")

    assert ANALYSIS_NO_PLUMBING_OVERWEIGHT_GUIDANCE in planning_analysis
    assert ANALYSIS_NO_TRANSIENT_HANDOFF_FAILURE_GUIDANCE in planning_analysis


def test_planning_templates_require_developer_executable_plans_not_meta_plans() -> None:
    planning = (TEMPLATES_ROOT / "planning.jinja").read_text(encoding="utf-8")
    planning_edit = (TEMPLATES_ROOT / "planning_edit.jinja").read_text(encoding="utf-8")
    planning_analysis = (TEMPLATES_ROOT / "planning_analysis.jinja").read_text(encoding="utf-8")

    assert PLANNING_EXECUTION_PLAN_GUIDANCE in planning
    assert PLANNING_EDIT_EXECUTION_PLAN_GUIDANCE in planning_edit
    assert PLANNING_ANALYSIS_NO_DOWNSTREAM_REPLANNING_GUIDANCE in planning_analysis
    assert "render_payload_path('PLAN', PLAN_PATH)" not in planning_analysis
    assert 'render_payload_path("PLAN", PLAN_PATH)' not in planning_analysis
    assert "PLAN_PATH" not in planning_edit


_MIN_WORKER_PROMPT_LEN = 50

_WORKER_TEMPLATE_BANNED_PHRASES = (
    "worktree-based",
    "per-worker worktree",
    "merge-back",
    "merge integration",
    "parallel worktree",
)


def test_worker_developer_template_renders_without_error(tmp_path: Path) -> None:
    """worker_developer.jinja must render through the real template engine.

    This fails loudly if the shared/_unattended_mode.jinja include is broken
    (e.g., pointing at a non-existent _unattended_mode.j2).
    """

    unit = WorkUnit(
        unit_id="unit-x",
        description="Test unit",
        allowed_directories=["src/x"],
    )
    policy = load_policy(tmp_path / ".agent")
    rendered = render_worker_prompt(unit, "base context here", policy.pipeline)
    assert "unit-x" in rendered
    assert "allowed_directories" in rendered.lower() or "src/x" in rendered
    assert len(rendered) > _MIN_WORKER_PROMPT_LEN


def test_worker_developer_template_contains_same_workspace_contract(tmp_path: Path) -> None:
    """worker_developer.jinja must instruct workers to share the same checkout
    and submit an artifact — not rely on git status or a per-worker worktree."""

    unit = WorkUnit(
        unit_id="unit-y",
        description="Same-workspace contract test",
        allowed_directories=["src/y"],
    )
    policy = load_policy(tmp_path / ".agent")
    rendered = render_worker_prompt(unit, "base context here", policy.pipeline)

    # Must reference the shared checkout (not per-worker worktrees)
    assert "repository checkout" in rendered or "same checkout" in rendered, (
        "worker prompt must mention 'repository checkout' or 'same checkout'"
    )

    # Must reference the per-worker namespace path
    assert ".agent/workers/" in rendered, (
        "worker prompt must reference '.agent/workers/' namespace path"
    )

    # Must instruct the worker to submit an artifact
    assert "submit_artifact" in rendered, (
        "worker prompt must instruct workers to call submit_artifact"
    )

    # Must not contain banned worktree-era phrases
    violations = [phrase for phrase in _WORKER_TEMPLATE_BANNED_PHRASES if phrase in rendered]
    assert violations == [], f"worker prompt must not contain banned phrases: {violations}"
