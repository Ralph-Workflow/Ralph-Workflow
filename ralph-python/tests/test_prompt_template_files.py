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
