"""Regression tests for file-backed prompt template assets."""

from __future__ import annotations

import json
from pathlib import Path

from ralph.mcp.tools.artifact import handle_submit_artifact

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


class _Workspace:
    def __init__(self, root: Path) -> None:
        self._root = root

    def absolute_path(self, path: str) -> str:
        return str((self._root / path).resolve())


def test_legacy_prompt_families_have_file_backed_jinja_templates() -> None:
    expected_templates = {
        "commit_message.jinja",
        "commit_simplified.jinja",
        "conflict_resolution.jinja",
        "conflict_resolution_fallback.jinja",
        "developer_iteration.jinja",
        "developer_iteration_continuation.jinja",
        "development_analysis.jinja",
        "development_commit_message.jinja",
        "fix_mode.jinja",
        "planning.jinja",
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


ANALYSIS_EXHAUSTIVE_FAILURE_GUIDANCE = (
    "**List every gap found** across all dimensions. Do not stop after the first problem."
)
ANALYSIS_OMISSION_GUIDANCE = (
    "3. **Cite concrete evidence.** File paths, function names, test names, command output,"
)
ANALYSIS_NO_FIRST_PROBLEM_GUIDANCE = (
    "Do not stop after the first problem."
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
    assert "approved" not in review_analysis
    assert "reject" not in review_analysis
    assert "Use `content` for a freshly generated JSON string." in development_analysis
    assert "Use `content` for a freshly generated JSON string." in review_analysis
    assert "content_path" not in development_analysis
    assert "content_path" not in review_analysis
    assert DEVELOPMENT_ANALYSIS_FRESH_SUBMIT_EXAMPLE in development_analysis
    assert REVIEW_ANALYSIS_FRESH_SUBMIT_EXAMPLE in review_analysis
    assert ANALYSIS_EXHAUSTIVE_FAILURE_GUIDANCE in development_analysis
    assert ANALYSIS_EXHAUSTIVE_FAILURE_GUIDANCE in review_analysis
    assert ANALYSIS_OMISSION_GUIDANCE in development_analysis
    assert ANALYSIS_OMISSION_GUIDANCE in review_analysis
    assert ANALYSIS_NO_FIRST_PROBLEM_GUIDANCE in development_analysis
    assert ANALYSIS_NO_FIRST_PROBLEM_GUIDANCE in review_analysis


def test_fix_and_developer_iteration_templates_use_analysis_context_partial() -> None:
    fix_template = (TEMPLATES_ROOT / "fix_mode.jinja").read_text(encoding="utf-8")
    dev_template = (TEMPLATES_ROOT / "developer_iteration.jinja").read_text(encoding="utf-8")

    assert "shared/_analysis_context" in fix_template
    assert "shared/_analysis_context" in dev_template
    assert "render_payload_section('ISSUES'" not in fix_template
    assert "render_payload_section('ANALYSIS FEEDBACK'" not in fix_template
    assert "render_payload_section('ANALYSIS FEEDBACK'" not in dev_template


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
            "what_came_up_short": ["A required verification step is missing."],
            "how_to_fix": ["Add the missing verification step and rerun checks."],
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
