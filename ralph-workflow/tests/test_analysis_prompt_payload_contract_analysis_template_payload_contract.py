"""Tests: analysis template payload contracts are correctly enforced."""

from __future__ import annotations

from pathlib import Path

import pytest

_TEMPLATES_DIR = Path(__file__).parent.parent / "ralph" / "prompts" / "templates"

_ANALYSIS_TEMPLATES = ["development_analysis.jinja", "review_analysis.jinja"]

_SUBAGENT_ANALYSIS_INPUTS = {
    "development_analysis.jinja": "PROMPT, PLAN, and the latest artifact",
    "review_analysis.jinja": "PROMPT, PLAN, and the latest artifact",
    "planning_analysis.jinja": "PROMPT and the current finalized plan or staged draft",
}

_RETRY_HINT_TEMPLATES = [
    "developer_iteration.jinja",
    "developer_iteration_continuation.jinja",
    "review.jinja",
    "planning.jinja",
    "fix_mode.jinja",
    "development_analysis.jinja",
    "review_analysis.jinja",
]


def _load(name: str) -> str:
    return (_TEMPLATES_DIR / name).read_text(encoding="utf-8")


class TestAnalysisTemplatePayloadContract:
    @pytest.mark.parametrize("name,input_phrase", _SUBAGENT_ANALYSIS_INPUTS.items())
    def test_analysis_templates_require_subagent_parallel_fanout_guidance(
        self, name: str, input_phrase: str
    ) -> None:
        source = _load(name)
        assert "## SUBAGENTS AND PARALLEL WORK" in source, (
            f"{name}: missing dedicated subagent/parallel work section"
        )
        required_intro = (
            "If your runtime provides a subagent, task, or parallel-agent mechanism, "
            "use it extensively"
        )
        assert required_intro in source, f"{name}: must explicitly require extensive subagent usage"
        assert "Fan out read-only subagents" in source, (
            f"{name}: must instruct read-only discovery/review fan-out"
        )
        assert "run independent analysis or verification work in parallel" in source, (
            f"{name}: must instruct safe parallel execution"
        )
        assert input_phrase in source, f"{name}: must direct retrieval of the core analysis inputs"
        assert "Only you, in the main session, may submit the final analysis artifact" in source, (
            f"{name}: artifact submission must remain in the main session"
        )

    @pytest.mark.parametrize("name", _ANALYSIS_TEMPLATES)
    def test_prompt_uses_render_payload_path_exact_form(self, name: str) -> None:
        source = _load(name)
        assert (
            "render_payload_path('PROMPT', PROMPT_PATH)" in source
            or 'render_payload_path("PROMPT", PROMPT_PATH)' in source
        ), f"{name}: PROMPT must use render_payload_path('PROMPT', PROMPT_PATH)"

    @pytest.mark.parametrize("name", _ANALYSIS_TEMPLATES)
    def test_prompt_does_not_use_render_payload_section(self, name: str) -> None:
        source = _load(name)
        assert "render_payload_section('PROMPT'" not in source, (
            f"{name}: render_payload_section('PROMPT' is forbidden"
        )
        assert 'render_payload_section("PROMPT"' not in source, (
            f'{name}: render_payload_section("PROMPT" is forbidden'
        )

    @pytest.mark.parametrize("name", _ANALYSIS_TEMPLATES)
    def test_plan_uses_render_payload_path_exact_form(self, name: str) -> None:
        source = _load(name)
        assert (
            "render_payload_path('PLAN', PLAN_PATH)" in source
            or 'render_payload_path("PLAN", PLAN_PATH)' in source
        ), f"{name}: PLAN must use render_payload_path('PLAN', PLAN_PATH)"

    @pytest.mark.parametrize("name", _ANALYSIS_TEMPLATES)
    def test_plan_does_not_use_render_payload_section(self, name: str) -> None:
        source = _load(name)
        assert "render_payload_section('PLAN'" not in source, (
            f"{name}: render_payload_section('PLAN' is forbidden"
        )
        assert 'render_payload_section("PLAN"' not in source, (
            f'{name}: render_payload_section("PLAN" is forbidden'
        )

    @pytest.mark.parametrize("name", _ANALYSIS_TEMPLATES)
    def test_latest_artifact_uses_render_payload_section_with_path(self, name: str) -> None:
        source = _load(name)
        assert (
            "render_payload_section('LATEST ARTIFACT', LATEST_ARTIFACT, LATEST_ARTIFACT_PATH)"
            in source
            or 'render_payload_section("LATEST ARTIFACT", LATEST_ARTIFACT, LATEST_ARTIFACT_PATH)'
            in source
        ), (
            f"{name}: LATEST ARTIFACT must use "
            "render_payload_section('LATEST ARTIFACT', LATEST_ARTIFACT, LATEST_ARTIFACT_PATH)"
        )
