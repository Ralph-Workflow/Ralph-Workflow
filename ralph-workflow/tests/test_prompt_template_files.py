"""Regression tests for file-backed prompt template assets."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_ROOT = REPO_ROOT / "ralph" / "prompts"
TEMPLATES_ROOT = PROMPTS_ROOT / "templates"
SHARED_ROOT = TEMPLATES_ROOT / "shared"


def test_legacy_prompt_families_have_file_backed_jinja_templates() -> None:
    expected_templates = {
        "analysis_system_prompt.jinja",
        "commit_message.jinja",
        "commit_simplified.jinja",
        "conflict_resolution.jinja",
        "conflict_resolution_fallback.jinja",
        "developer_iteration.jinja",
        "developer_iteration_continuation.jinja",
        "development_analysis.jinja",
        "development_commit_message.jinja",
        "fix_analysis_system_prompt.jinja",
        "fix_mode.jinja",
        "parallel_dev_worker.jinja",
        "parallel_planning.jinja",
        "parallel_verifier.jinja",
        "planning.jinja",
        "review.jinja",
        "review_analysis.jinja",
    }

    actual_templates = {path.name for path in TEMPLATES_ROOT.glob("*.jinja")}

    assert expected_templates <= actual_templates


def test_legacy_shared_partials_have_one_jinja_file_each() -> None:
    expected_partials = {
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
    assert 'prompt_template = "development_analysis.jinja"' in artifacts_toml
    assert 'prompt_template = "review_analysis.jinja"' in artifacts_toml
    assert 'prompt_template = "development_commit_message.jinja"' in artifacts_toml
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


ANALYSIS_CONTENT_PATH_GUIDANCE = (
    "Use `content_path` only when resubmitting a JSON file that already exists on disk."
)
ANALYSIS_EXHAUSTIVE_FAILURE_GUIDANCE = (
    "Include every issue that contributed to the failing status."
)
ANALYSIS_OMISSION_GUIDANCE = (
    "If you omit a real failure cause, the analysis artifact is incomplete."
)
ANALYSIS_NO_FIRST_PROBLEM_GUIDANCE = (
    "Do not stop after the first problem if more issues were found."
)
DEVELOPMENT_ANALYSIS_FRESH_SUBMIT_EXAMPLE = (
    '"artifact_type":"development_analysis_decision",'
    '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"'
)
REVIEW_ANALYSIS_FRESH_SUBMIT_EXAMPLE = (
    '"artifact_type":"review_analysis_decision",'
    '"content":"{\\"status\\":\\"completed\\",\\"summary\\":\\"...\\"}"'
)


def test_analysis_templates_require_exact_artifact_types_and_detailed_fix_sections() -> None:
    development_analysis = (TEMPLATES_ROOT / "development_analysis.jinja").read_text(
        encoding="utf-8"
    )
    review_analysis = (TEMPLATES_ROOT / "review_analysis.jinja").read_text(encoding="utf-8")

    assert 'artifact_type="development_analysis_decision"' in development_analysis
    assert 'artifact_type="review_analysis_decision"' in review_analysis
    assert "what_came_up_short" in development_analysis
    assert "how_to_fix" in development_analysis
    assert "what_came_up_short" in review_analysis
    assert "how_to_fix" in review_analysis
    assert "Not submitting the analysis artifact is a FAILURE." in development_analysis
    assert "Not submitting the analysis artifact is a FAILURE." in review_analysis
    assert "SUBMIT_ARTIFACT_TOOL_REFERENCE" in development_analysis
    assert "SUBMIT_ARTIFACT_TOOL_REFERENCE" in review_analysis
    assert "Use `content` for a freshly generated JSON string." in development_analysis
    assert "Use `content` for a freshly generated JSON string." in review_analysis
    assert ANALYSIS_CONTENT_PATH_GUIDANCE in development_analysis
    assert ANALYSIS_CONTENT_PATH_GUIDANCE in review_analysis
    assert "Never send both `content` and `content_path` in the same call." in development_analysis
    assert "Never send both `content` and `content_path` in the same call." in review_analysis
    assert DEVELOPMENT_ANALYSIS_FRESH_SUBMIT_EXAMPLE in development_analysis
    assert REVIEW_ANALYSIS_FRESH_SUBMIT_EXAMPLE in review_analysis
    assert ANALYSIS_EXHAUSTIVE_FAILURE_GUIDANCE in development_analysis
    assert ANALYSIS_EXHAUSTIVE_FAILURE_GUIDANCE in review_analysis
    assert ANALYSIS_OMISSION_GUIDANCE in development_analysis
    assert ANALYSIS_OMISSION_GUIDANCE in review_analysis
    assert ANALYSIS_NO_FIRST_PROBLEM_GUIDANCE in development_analysis
    assert ANALYSIS_NO_FIRST_PROBLEM_GUIDANCE in review_analysis


def test_review_and_fix_templates_define_explicit_review_handoff_contracts() -> None:
    review_template = (TEMPLATES_ROOT / "review.jinja").read_text(encoding="utf-8")
    fix_template = (TEMPLATES_ROOT / "fix_mode.jinja").read_text(encoding="utf-8")

    assert 'artifact_type="issues"' in review_template
    assert "what_came_up_short" in review_template
    assert "how_to_fix" in review_template
    assert "FIX_RESULT" in review_template
    assert "fix_result" not in fix_template
    assert "submit" not in fix_template.lower()
