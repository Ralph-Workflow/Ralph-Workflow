"""Tests: analysis templates never inline PROMPT or PLAN regardless of content size."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import ralph.prompts.materialize as materialize_module
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

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
