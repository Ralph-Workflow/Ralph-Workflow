"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.config.enums import (
    Verbosity,
)
from ralph.display.context import make_display_context
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import (
    CommitEffect,
    InvokeAgentEffect,
    PreparePromptEffect,
    SaveCheckpointEffect,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    PolicyBundle,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pytest import MonkeyPatch


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
        f"---\ntype: plan\n---\n## Work\n### [S-1] Preserve {context}\nInspect the existing plan handoff.\nType: discovery\nLocation: .agent/artifacts/plan.md\n",
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
        f"---\ntype: plan\n---\n## Work\n### [S-1] Preserve {context}\nInspect the existing plan handoff.\nType: discovery\nLocation: .agent/artifacts/plan.md\n",
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _stub_workspace_scope_and_policy(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner_module, "resolve_workspace_scope", lambda: WorkspaceScope(tmp_path))
    monkeypatch.setattr(
        runner_module, "load_policy_or_die", lambda _path: _load_default_policy_bundle()
    )


def test_materialize_agent_prompt_if_needed_routes_injected_phase_materializer(
    tmp_path: Path,
) -> None:
    """The ``materialize_fn`` parameter routes an injected phase materializer."""
    policy_bundle = _load_default_policy_bundle()
    workspace = FsWorkspace(tmp_path)
    workspace.write("PROMPT.md", "Create a fresh plan")
    effect = InvokeAgentEffect(
        agent_name="claude",
        phase="planning",
        prompt_file="PROMPT.md",
        drain="planning",
        chain_name="planning",
    )
    state = PipelineState(phase="planning", previous_phase=None)
    registry = MagicMock()
    registry.get.return_value = None
    calls: list[dict[str, object]] = []

    def fake_materialize(**kwargs: object) -> str:
        calls.append(kwargs)
        return "fake-prompt.md"

    runner_module.materialize_agent_prompt_if_needed(
        effect,
        state,
        workspace,
        policy_bundle,
        registry,
        materialize_fn=fake_materialize,
    )

    assert len(calls) == 1
    assert calls[0]["phase"] == "planning"


class TestExecuteEffect:
    def test_save_checkpoint_returns_checkpoint_event(self) -> None:
        result = runner_module.execute_effect(
            SaveCheckpointEffect(), MagicMock(), WorkspaceScope("/tmp/worktree")
        )

        assert result == PipelineEvent.CHECKPOINT_SAVED

    def test_execute_effect_with_optional_display_only_passes_supported_kwargs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_execute_effect(
            effect: object, config: object, workspace_scope: object, *, display: object
        ) -> object:
            captured["effect"] = effect
            captured["config"] = config
            captured["workspace_scope"] = workspace_scope
            captured["display"] = display
            return PipelineEvent.AGENT_SUCCESS

        monkeypatch.setattr(runner_module, "execute_effect", fake_execute_effect)
        effect = InvokeAgentEffect(agent_name="planning", phase="planning", prompt_file="plan.md")
        config = MagicMock()
        workspace_scope = WorkspaceScope("/tmp/worktree")
        display = MagicMock()
        state = PipelineState(phase="planning")

        result = runner_module.execute_effect_with_optional_display(
            effect,
            config,
            workspace_scope,
            display=display,
            state=state,
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured == {
            "effect": effect,
            "config": config,
            "workspace_scope": workspace_scope,
            "display": display,
        }

    def test_execute_effect_with_optional_display_passes_context_to_kwargs_handlers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def fake_execute_effect(
            effect: object, config: object, workspace_scope: object, **kwargs: object
        ) -> object:
            captured["effect"] = effect
            captured["config"] = config
            captured["workspace_scope"] = workspace_scope
            captured.update(kwargs)
            return PipelineEvent.AGENT_SUCCESS

        monkeypatch.setattr(runner_module, "execute_effect", fake_execute_effect)
        effect = InvokeAgentEffect(agent_name="planning", phase="planning", prompt_file="plan.md")
        config = MagicMock()
        workspace_scope = WorkspaceScope("/tmp/worktree")
        display_context = make_display_context()
        state = PipelineState(phase="planning")

        result = runner_module.execute_effect_with_optional_display(
            effect,
            config,
            workspace_scope,
            display=None,
            display_context=display_context,
            verbosity=Verbosity.QUIET,
            state=state,
            policy_bundle=_load_default_policy_bundle(),
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured["effect"] is effect
        assert captured["config"] is config
        assert captured["workspace_scope"] == workspace_scope
        assert captured["display"] is None
        assert captured["display_context"] is display_context
        assert captured["verbosity"] == Verbosity.QUIET
        assert captured["state"] == state
        assert isinstance(captured["policy_bundle"], PolicyBundle)

    def test_commit_effect_delegates_to_commit_handler(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, bool] = {}

        def stub_commit(
            effect: object,
            create_commit: object,
            stage_all: object,
            repo_root: object,
            display: object = None,
            *,
            verbosity: object = None,
            phase_name: object = "commit",
            state: object = None,
            pipeline_policy: object = None,
            **opts: object,
        ) -> PipelineEvent:
            del create_commit, stage_all, repo_root, display, verbosity, phase_name
            del state, pipeline_policy
            assert callable(opts["has_residual_work_fn"])
            captured["called"] = True
            captured["message_file"] = effect.message_file
            return PipelineEvent.COMMIT_SUCCESS

        monkeypatch.setattr(runner_module, "execute_commit_effect", stub_commit)
        result = runner_module.execute_effect(
            CommitEffect(message_file="foo"), MagicMock(), WorkspaceScope("/tmp/worktree")
        )

        assert result == PipelineEvent.COMMIT_SUCCESS
        assert captured.get("called")
        assert captured.get("message_file") == "foo"

    def test_unknown_effect_returns_failure(self) -> None:
        result = runner_module.execute_effect(
            PreparePromptEffect(phase="planning", iteration=0),
            MagicMock(),
            WorkspaceScope("/tmp/worktree"),
        )

        assert result == PipelineEvent.AGENT_FAILURE

    def test_execute_effect_forwards_run_id_to_execute_agent_effect(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-2 run_id threading: ``execute_effect``'s InvokeAgentEffect branch
        forwards a caller-supplied ``run_id`` into ``execute_agent_effect``
        instead of letting it be silently regenerated there."""
        captured: dict[str, object] = {}

        def fake_execute_agent_effect(
            effect: object,
            config: object,
            pipeline_deps: object,
            workspace_scope: object,
            **kwargs: object,
        ) -> PipelineEvent:
            del effect, config, pipeline_deps, workspace_scope
            captured.update(kwargs)
            return PipelineEvent.AGENT_SUCCESS

        monkeypatch.setattr(runner_module, "execute_agent_effect", fake_execute_agent_effect)
        effect = InvokeAgentEffect(agent_name="planning", phase="planning", prompt_file="plan.md")

        result = runner_module.execute_effect(
            effect,
            MagicMock(),
            WorkspaceScope("/tmp/worktree"),
            pipeline_deps=MagicMock(),
            run_id="direct-execute-effect-run-id",
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured["run_id"] == "direct-execute-effect-run-id"

    def test_execute_effect_with_optional_display_forwards_run_id(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-2 run_id threading: the public wrapper chain forwards run_id
        through to whatever ``execute_effect`` resolves to at call time."""
        captured: dict[str, object] = {}

        def fake_execute_effect(
            effect: object, config: object, workspace_scope: object, **kwargs: object
        ) -> object:
            del effect, config, workspace_scope
            captured.update(kwargs)
            return PipelineEvent.AGENT_SUCCESS

        monkeypatch.setattr(runner_module, "execute_effect", fake_execute_effect)
        effect = InvokeAgentEffect(agent_name="planning", phase="planning", prompt_file="plan.md")

        result = runner_module.execute_effect_with_optional_display(
            effect,
            MagicMock(),
            WorkspaceScope("/tmp/worktree"),
            display=None,
            display_context=make_display_context(),
            verbosity=Verbosity.QUIET,
            run_id="wrapper-chain-run-id",
        )

        assert result == PipelineEvent.AGENT_SUCCESS
        assert captured["run_id"] == "wrapper-chain-run-id"
