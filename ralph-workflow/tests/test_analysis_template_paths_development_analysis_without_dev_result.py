"""Tests: analysis templates never inline PROMPT or PLAN regardless of content size."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import ralph.prompts.materialize as materialize_module
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
    workspace.write(".agent/artifacts/development_result.json", _MINIMAL_DEV_RESULT)
    with patch.object(materialize_module, "_git_diff", return_value="diff"):
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


def _render_development_analysis_no_dev_result(
    tmp_path: Path,
    *,
    prompt_content: str = _TINY_PROMPT,
) -> str:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", prompt_content)
    _write_plan_handoff(workspace)
    with patch.object(materialize_module, "_git_diff", return_value="diff"):
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


class TestDevelopmentAnalysisWithoutDevResult:
    """Verify development_analysis prompt renders correctly when development_result is absent.

    Prompt materialization for development_analysis must not crash and must still reference
    CURRENT_PROMPT.md and PLAN.
    """

    def test_renders_without_crash_when_dev_result_absent(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis_no_dev_result(tmp_path)
        assert len(rendered) > 0

    def test_prompt_reference_present_when_dev_result_absent(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis_no_dev_result(tmp_path)
        assert "CURRENT_PROMPT.md" in rendered

    def test_plan_reference_present_when_dev_result_absent(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis_no_dev_result(tmp_path)
        assert "Read the complete plan from file at" in rendered
