"""Tests: analysis templates never inline PROMPT or PLAN regardless of content size."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import ralph.prompts.materialize as materialize_module
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

_TINY_PROMPT = "Implement the feature."
_LARGE_CONTENT = "X" * (100 * 1024 + 1)

_MINIMAL_DEV_RESULT = json.dumps(
    {
        "type": "development_result",
        "content": {
            "status": "completed",
            "summary": "Done.",
            "files_changed": "- src/app.py",
        },
    }
)

_MINIMAL_ISSUES = json.dumps(
    {
        "type": "issues",
        "content": {
            "status": "issues_found",
            "summary": "Minor issue.",
            "issues": [{"path": "a.py", "severity": "low", "summary": "lint"}],
            "what_came_up_short": ["linting"],
            "how_to_fix": ["run lint"],
        },
    }
)

_TEMPLATES_DIR = (
    Path(__file__).parent.parent / "ralph" / "prompts" / "templates"
)
_MIN_EXPECTED_ANALYSIS_TEMPLATES = 2


def _render_development_analysis(
    tmp_path: Path,
    *,
    prompt_content: str = _TINY_PROMPT,
) -> str:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", prompt_content)
    workspace.write(".agent/artifacts/development_result.json", _MINIMAL_DEV_RESULT)
    with patch.object(materialize_module, "_git_diff", return_value="diff"):
        path = materialize_prompt_for_phase(
            phase="development_analysis",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        )
    return workspace.read(path)


def _render_review_analysis(
    tmp_path: Path,
    *,
    prompt_content: str = _TINY_PROMPT,
) -> str:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", prompt_content)
    workspace.write(".agent/artifacts/issues.json", _MINIMAL_ISSUES)
    with patch.object(materialize_module, "_git_diff", return_value="diff"):
        path = materialize_prompt_for_phase(
            phase="review_analysis",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.REVIEW),
            workspace_root=tmp_path,
        )
    return workspace.read(path)


class TestDevelopmentAnalysisNeverInlinesPromptOrPlan:
    def test_tiny_prompt_is_not_inlined(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis(tmp_path, prompt_content=_TINY_PROMPT)
        assert _TINY_PROMPT not in rendered
        assert "CURRENT_PROMPT.md" in rendered

    def test_large_prompt_is_not_inlined(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis(tmp_path, prompt_content=_LARGE_CONTENT)
        assert _LARGE_CONTENT not in rendered
        assert "CURRENT_PROMPT.md" in rendered

    def test_prompt_reference_has_read_instruction(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis(tmp_path)
        assert "Read the complete prompt from file at" in rendered

    def test_plan_reference_has_read_instruction(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis(tmp_path)
        assert "Read the complete plan from file at" in rendered

    def test_latest_artifact_body_appears_in_rendered_or_has_path_reference(
        self, tmp_path: Path
    ) -> None:
        rendered = _render_development_analysis(tmp_path)
        has_inline = "Done." in rendered
        has_path = "DEVELOPMENT_RESULT.md" in rendered
        assert has_inline or has_path, "LATEST ARTIFACT must be present (inline or path ref)"


class TestReviewAnalysisNeverInlinesPromptOrPlan:
    def test_tiny_prompt_is_not_inlined(self, tmp_path: Path) -> None:
        rendered = _render_review_analysis(tmp_path, prompt_content=_TINY_PROMPT)
        assert _TINY_PROMPT not in rendered
        assert "CURRENT_PROMPT.md" in rendered

    def test_large_prompt_is_not_inlined(self, tmp_path: Path) -> None:
        rendered = _render_review_analysis(tmp_path, prompt_content=_LARGE_CONTENT)
        assert _LARGE_CONTENT not in rendered
        assert "CURRENT_PROMPT.md" in rendered

    def test_prompt_reference_has_read_instruction(self, tmp_path: Path) -> None:
        rendered = _render_review_analysis(tmp_path)
        assert "Read the complete prompt from file at" in rendered

    def test_plan_reference_has_read_instruction(self, tmp_path: Path) -> None:
        rendered = _render_review_analysis(tmp_path)
        assert "Read the complete plan from file at" in rendered

    def test_latest_artifact_body_appears_in_rendered_or_has_path_reference(
        self, tmp_path: Path
    ) -> None:
        rendered = _render_review_analysis(tmp_path)
        has_inline = "Minor issue." in rendered or "lint" in rendered
        has_path = "ISSUES.md" in rendered
        assert has_inline or has_path, "LATEST ARTIFACT must be present (inline or path ref)"


class TestAnalysisTemplatesStructuralInvariants:
    """Verify analysis template source never uses render_payload_section for PROMPT or PLAN.

    These tests read the raw .jinja source files and assert structural invariants that
    protect against regression — even if the rendered output happens to look correct.
    """

    def _analysis_templates(self) -> list[Path]:
        return sorted(_TEMPLATES_DIR.glob("*_analysis.jinja"))

    def test_at_least_two_analysis_templates_exist(self) -> None:
        templates = self._analysis_templates()
        count = len(templates)
        assert count >= _MIN_EXPECTED_ANALYSIS_TEMPLATES, (
            f"Expected >={_MIN_EXPECTED_ANALYSIS_TEMPLATES} *_analysis.jinja templates,"
            f" found: {templates}"
        )

    def test_prompt_uses_render_payload_path_not_section(self) -> None:
        for template in self._analysis_templates():
            source = template.read_text(encoding="utf-8")
            uses_path = (
                "render_payload_path('PROMPT'" in source
                or 'render_payload_path("PROMPT"' in source
            )
            assert uses_path, (
                f"{template.name}: PROMPT must use render_payload_path"
            )
            assert "render_payload_section('PROMPT'" not in source, (
                f"{template.name}: render_payload_section('PROMPT' is forbidden"
            )
            assert 'render_payload_section("PROMPT"' not in source, (
                f'{template.name}: render_payload_section("PROMPT" is forbidden'
            )

    def test_plan_uses_render_payload_path_not_section(self) -> None:
        for template in self._analysis_templates():
            source = template.read_text(encoding="utf-8")
            uses_path = (
                "render_payload_path('PLAN'" in source
                or 'render_payload_path("PLAN"' in source
            )
            assert uses_path, (
                f"{template.name}: PLAN must use render_payload_path"
            )
            assert "render_payload_section('PLAN'" not in source, (
                f"{template.name}: render_payload_section('PLAN' is forbidden"
            )
            assert 'render_payload_section("PLAN"' not in source, (
                f'{template.name}: render_payload_section("PLAN" is forbidden'
            )
