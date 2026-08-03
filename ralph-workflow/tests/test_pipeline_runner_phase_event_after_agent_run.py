"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.pipeline import phase_agent_handler as phase_agent_handler_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    InvokeAgentEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch

    from ralph.policy.models import (
        PolicyBundle,
    )


DEVELOPER_ITERATIONS = 5
REVIEWER_PASSES = 2
SECOND_ITERATION = 2
INTERRUPT_EXIT_CODE = 130
_TRUNCATED_TEXT_MAX = runner_module.MAX_TEXT_LENGTH + 1  # content + ellipsis
_TRUNCATED_RESULT_BRIEF_MAX = runner_module.MAX_TOOL_RESULT_BRIEF + 1  # content + ellipsis
_TRUNCATED_METADATA_MAX = runner_module.MAX_METADATA_SUMMARY_LENGTH + 1  # content + ellipsis
_AVAILABLE_WIDTH_FLOOR = 40
_TRUNCATE_RESULT_LEN = 6  # 5 chars + 1 ellipsis char


@lru_cache(maxsize=1)
def _load_default_policy_bundle() -> PolicyBundle:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir)


def _policy_bundle_with_loop_counter_max(counter_name: str, default_max: int) -> PolicyBundle:
    bundle = _load_default_policy_bundle()
    loop_counters = dict(bundle.pipeline.loop_counters)
    loop_counters[counter_name] = loop_counters[counter_name].model_copy(
        update={"default_max": default_max}
    )
    return bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"loop_counters": loop_counters})}
    )


def _registry_factory(return_value: object) -> object:
    class Registry:
        @classmethod
        def from_config(cls, config: object) -> object:
            instance = MagicMock()
            instance.get.return_value = return_value
            return instance

    return Registry


def _install_runner_display_context(
    monkeypatch: MonkeyPatch,
    *,
    width: int = 120,
) -> Console:
    console = Console(record=True, force_terminal=False, width=width, color_system=None)
    ctx = make_display_context(
        console=console,
        force_width=width,
    )
    monkeypatch.setattr(runner_module, "make_display_context", lambda **_kwargs: ctx)
    return console


def _config_with_agents(
    *,
    agent_chains: dict[str, list[str]],
    agent_drains: dict[str, str],
) -> object:
    config = MagicMock()
    config.agent_chains = agent_chains
    config.agent_drains = agent_drains
    return config


def _write_minimal_plan_artifacts(
    root: Path,
    *,
    context: str = "Existing plan",
) -> None:
    (root / ".agent" / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / ".agent" / "artifacts" / "plan.md").write_text(
        f"---\ntype: plan\nschema_version: 1\nintent_verb: modify\n---\n## Summary\n{context}\n",
        encoding="utf-8",
    )
    (root / ".agent" / "PLAN.md").write_text(
        f"# Execution Plan\n\n{context}.\n",
        encoding="utf-8",
    )


def _write_minimal_plan_draft(root: Path, *, context: str = "Existing draft") -> None:
    artifact_dir = root / ".agent" / "artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / ".plan.draft.md").write_text(
        f"---\ntype: plan\nschema_version: 1\nintent_verb: modify\n---\n## Summary\n{context}\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


class TestPhaseEventAfterAgentRun:
    @pytest.mark.parametrize(
        ("phase", "event", "artifact_path", "document", "expected_title", "expected_text"),
        [
            (
                "planning",
                PipelineEvent.AGENT_SUCCESS,
                ".agent/artifacts/plan.md",
                "# PLAN\n\nPlanning handoff rendered from runner.\n",
                "PLAN",
                "Planning handoff rendered from runner.",
            ),
            (
                "development",
                PipelineEvent.AGENT_SUCCESS,
                ".agent/artifacts/development_result.md",
                "# DEVELOPMENT RESULT\n\nDevelopment result rendered from runner.\n",
                "DEVELOPMENT RESULT",
                "Development result rendered from runner.",
            ),
            (
                "development_analysis",
                PipelineEvent.ANALYSIS_LOOPBACK,
                ".agent/artifacts/development_analysis_decision.md",
                "# ANALYSIS: development_analysis\n\nAnalysis result rendered from runner.\n",
                "ANALYSIS: development_analysis",
                "Analysis result rendered from runner.",
            ),
        ],
    )
    def test_renders_phase_artifact_handoff_after_phase_handler_returns(
        self,
        tmp_path: Path,
        monkeypatch: MonkeyPatch,
        phase: str,
        event: PipelineEvent,
        artifact_path: str,
        document: str,
        expected_title: str,
        expected_text: str,
    ) -> None:
        registry = MagicMock()
        registry.from_config.return_value = MagicMock()
        monkeypatch.setattr(phase_agent_handler_module, "AgentRegistry", registry)
        monkeypatch.setattr(
            phase_agent_handler_module, "ChainManager", MagicMock(return_value=MagicMock())
        )
        monkeypatch.setattr(
            phase_agent_handler_module, "handle_phase", lambda _effect, _ctx: [event]
        )

        artifact_file = tmp_path / artifact_path
        artifact_file.parent.mkdir(parents=True, exist_ok=True)
        artifact_file.write_text(document, encoding="utf-8")

        output = io.StringIO()
        console = Console(file=output, force_terminal=False, color_system=None, width=120)
        display = ParallelDisplay(make_display_context(console=console, env={}))
        policy_bundle = _load_default_policy_bundle()
        workspace = MagicMock()
        workspace.absolute_path.side_effect = lambda path: str(tmp_path / path)

        returned_event = runner_module.phase_event_after_agent_run(
            effect=InvokeAgentEffect(agent_name="claude", phase=phase, prompt_file=f"{phase}.md"),
            config=MagicMock(),
            policy_bundle=policy_bundle,
            workspace=workspace,
            workspace_scope=WorkspaceScope(root=tmp_path, allowed_roots=[tmp_path]),
            display=display,
        )

        assert returned_event == event
        rendered = output.getvalue()
        assert expected_title in rendered
        assert expected_text in rendered
