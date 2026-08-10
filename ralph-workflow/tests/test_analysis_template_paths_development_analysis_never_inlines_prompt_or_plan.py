"""Tests: analysis templates never inline PROMPT or PLAN regardless of content size."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

_TINY_PROMPT = "Implement the feature."
_LARGE_CONTENT = "X" * (100 * 1024 + 1)

_MINIMAL_DEV_RESULT = """---
type: development_result
status: completed
---
## Summary
Done.

## Files Changed
- src/app.py

## Proof
- [S-1] Added the regression coverage.
"""


_TEMPLATES_DIR = Path(__file__).parent.parent / "ralph" / "prompts" / "templates"
_MIN_EXPECTED_ANALYSIS_TEMPLATES = 2


def _write_plan_handoff(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/PLAN.md",
        "# Execution Plan\n\n"
        "1. Add the missing regression test.\n"
        "2. Tighten prompt preconditions.\n",
    )


def _render_development_analysis(
    tmp_path: Path,
    *,
    prompt_content: str = _TINY_PROMPT,
) -> str:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", prompt_content)
    _write_plan_handoff(workspace)
    workspace.write(".agent/artifacts/development_result.md", _MINIMAL_DEV_RESULT)
    path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="development_analysis",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
        ),
    )
    return workspace.read(path)


@pytest.mark.timeout_seconds(5)
class TestDevelopmentAnalysisNeverInlinesPromptOrPlan:
    def test_tiny_prompt_is_not_inlined(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis(tmp_path, prompt_content=_TINY_PROMPT)
        assert _TINY_PROMPT not in rendered
        assert "PRODUCT_CRITERIA.md" in rendered

    def test_large_prompt_is_not_inlined(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis(tmp_path, prompt_content=_LARGE_CONTENT)
        assert _LARGE_CONTENT not in rendered
        assert "PRODUCT_CRITERIA.md" in rendered

    def test_prompt_reference_has_read_instruction(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis(tmp_path)
        assert "Read the complete prompt from file at" in rendered

    def test_plan_reference_has_read_instruction(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis(tmp_path)
        assert "Read the complete plan from file at" in rendered

    def test_implementer_artifact_is_excluded_from_rendered_prompt(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis(tmp_path)
        assert "Done." not in rendered
        assert ".agent/artifacts/development_result.md" not in rendered
