"""Tests: analysis template payload contracts are correctly enforced."""

from __future__ import annotations

from pathlib import Path

import pytest

_TEMPLATES_DIR = Path(__file__).parent.parent / "ralph" / "prompts" / "templates"

_ANALYSIS_TEMPLATES = ["development_analysis.jinja", "review_analysis.jinja"]

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
