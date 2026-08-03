"""Tests for ralph/pipeline/runner.py — pipeline runner."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from ralph.display.context import make_display_context
from ralph.pipeline import run_loop
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import CommitEffect
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


def _policy_bundle_with_loop_counter(counter_name: str, default_max: int) -> PolicyBundle:
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


class TestCycleBaselineLifecycle:
    """Regression tests: cycle baseline is cleared at dev-cycle boundaries."""

    def test_run_clears_baseline_at_teardown_on_success(
        self, monkeypatch: MonkeyPatch, tmp_path: Path
    ) -> None:
        cleared: list[Path] = []
        loop_ctx = MagicMock()
        loop_ctx.workspace_scope.root = tmp_path
        loop_ctx.monitor_stop = None
        loop_ctx.pro_watcher = None
        loop_ctx.heartbeat_client = None
        loop_ctx.catchup_worker = None
        loop_ctx.process_teardown = lambda: None
        loop_ctx.effective_pipeline_subscriber = None
        loop_ctx.active_display = MagicMock()
        loop_ctx.display_context = MagicMock()
        monkeypatch.setattr(run_loop._runner_module, "clear_cycle_baseline", cleared.append)
        monkeypatch.setattr(run_loop, "emit_final_summary", lambda *_args, **_kwargs: None)

        run_loop._cleanup_pipeline(loop_ctx, lambda: None, lambda: None, lambda: None, MagicMock())

        assert cleared == [tmp_path], "run() must clear .agent/start_commit at pipeline teardown"

    def test_run_clears_baseline_at_teardown_on_failure(
        self, monkeypatch: MonkeyPatch, tmp_path: Path
    ) -> None:
        cleared: list[Path] = []
        loop_ctx = MagicMock()
        loop_ctx.workspace_scope.root = tmp_path
        loop_ctx.monitor_stop = None
        loop_ctx.pro_watcher = None
        loop_ctx.heartbeat_client = None
        loop_ctx.catchup_worker = None
        loop_ctx.process_teardown = lambda: None
        loop_ctx.effective_pipeline_subscriber = None
        loop_ctx.active_display = MagicMock()
        loop_ctx.display_context = MagicMock()
        monkeypatch.setattr(run_loop._runner_module, "clear_cycle_baseline", cleared.append)
        monkeypatch.setattr(run_loop, "emit_final_summary", lambda *_args, **_kwargs: None)

        run_loop._cleanup_pipeline(loop_ctx, lambda: None, lambda: None, lambda: None, MagicMock())

        assert cleared == [tmp_path], (
            "run() must call clear_cycle_baseline in its finally/teardown block"
        )

    def test_run_pipeline_step_clears_baseline_after_development_commit_success(
        self, monkeypatch: MonkeyPatch, tmp_path: Path
    ) -> None:
        cleared: list[Path] = []
        workspace_scope = WorkspaceScope(tmp_path)
        commit_phase = MagicMock()
        commit_phase.role = "commit"
        monkeypatch.setattr(runner_module, "clear_cycle_baseline", cleared.append)
        monkeypatch.setattr(
            runner_module,
            "_integrate_on_phase_transition",
            lambda **_kwargs: None,
        )

        runner_module._maybe_auto_integrate(
            effect=CommitEffect(message_file="/dev/null"),
            event=PipelineEvent.COMMIT_SUCCESS,
            commit_phase_def=commit_phase,
            config=MagicMock(),
            workspace_scope=workspace_scope,
            state=MagicMock(),
            display=MagicMock(),
        )

        assert cleared == [tmp_path], (
            "clear_cycle_baseline must be called after development_commit COMMIT_SUCCESS"
        )
