from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.skills import state_store
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


def _render_development_analysis_no_dev_result(
    tmp_path: Path,
    *,
    prompt_content: str = _TINY_PROMPT,
) -> str:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", prompt_content)
    _write_plan_handoff(workspace)
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
    @classmethod
    def setup_class(cls) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_home = Path(tmp_dir)

            old_default = state_store.DEFAULT_STATE_PATH
            state_store.DEFAULT_STATE_PATH = (
                tmp_home / ".config" / "ralph-workflow-capabilities.json"
            )
            old_home = os.environ.get("HOME")
            old_xdg = os.environ.get("XDG_CONFIG_HOME")
            os.environ["HOME"] = str(tmp_home)
            os.environ["XDG_CONFIG_HOME"] = str(tmp_home / ".config")
            try:
                _render_development_analysis_no_dev_result(tmp_home)
            finally:
                state_store.DEFAULT_STATE_PATH = old_default
                if old_home is not None:
                    os.environ["HOME"] = old_home
                else:
                    os.environ.pop("HOME", None)
                if old_xdg is not None:
                    os.environ["XDG_CONFIG_HOME"] = old_xdg
                else:
                    os.environ.pop("XDG_CONFIG_HOME", None)

    def test_renders_without_crash_when_dev_result_absent(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis_no_dev_result(tmp_path)
        assert len(rendered) > 0

    def test_prompt_reference_present_when_dev_result_absent(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis_no_dev_result(tmp_path)
        assert "PRODUCT_CRITERIA.md" in rendered

    def test_plan_reference_present_when_dev_result_absent(self, tmp_path: Path) -> None:
        rendered = _render_development_analysis_no_dev_result(tmp_path)
        assert "Read the complete plan from file at" in rendered
