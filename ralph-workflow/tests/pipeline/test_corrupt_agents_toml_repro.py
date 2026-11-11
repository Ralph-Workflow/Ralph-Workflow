from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from loguru import logger
from rich.console import Console

from ralph.agents.registry import AgentRegistry
from ralph.config import reset_fallback_warning_registry
from ralph.config.enums import Verbosity
from ralph.config.loader import load_config
from ralph.display.auto_integrate_message import OperationDisplayState
from ralph.display.context import make_display_context
from ralph.git.rebase.rebase import RebaseSuccess
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline import auto_integrate_rebase_merge as merge_mod
from ralph.pipeline import resolve_conflicted_rebase, runner
from ralph.pipeline.auto_integrate_agent import build_agent_rebase_stop_resolver
from ralph.pipeline.conflict_resolution import (
    RebaseStop,
    ResolutionTerminationReason,
    bind_active_rebase_resolution_session,
)
from ralph.pipeline.conflict_resolution.session import ResolutionSession
from ralph.pipeline.effects import CommitEffect, Effect, InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.run_loop import LoopContext, resolve_paused_rebase
from ralph.pipeline.run_loop import run as pipeline_run
from ralph.policy.loader import default_dir, load_policy
from ralph.workspace.scope import WorkspaceScope


@pytest.fixture(autouse=True)
def _clear_fallback_registry() -> None:
    reset_fallback_warning_registry()


def _seed_workspace(root: Path, *, prompt: str = "Test project prompt\n") -> None:
    agent_dir = root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "ralph-workflow.toml").write_text("[general]\n", encoding="utf-8")
    (root / "PROMPT.md").write_text(prompt, encoding="utf-8")


def _setup_corrupt_agents_toml(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: str = "[agents\ninvalid toml syntax = = =\n",
) -> tuple[Path, bytes]:
    config_home = tmp_path / "isolated-xdg-config"
    config_home.mkdir(parents=True, exist_ok=True)
    agents_file = config_home / "ralph-workflow-agents.toml"
    agents_file.write_text(content, encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    return agents_file, agents_file.read_bytes()


def test_corrupt_agents_toml_rebase_stop_resolver_launches_with_default_agents(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _seed_workspace(repo_root)

    agents_file, original_bytes = _setup_corrupt_agents_toml(monkeypatch, tmp_path)

    policy = load_policy(default_dir())
    cfg = load_config(config_path=repo_root / ".agent" / "ralph-workflow.toml")
    reg = AgentRegistry.from_config(cfg)

    mock_display = MagicMock()
    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=policy,
        registry=reg,
        display=mock_display,
        config=cfg,
        pipeline_deps=MagicMock(),
        workspace_scope=WorkspaceScope(repo_root),
    )

    launched_pipeline_kwargs: list[dict[str, Any]] = []

    def _fake_run_pipeline(**kwargs: Any) -> bool:
        launched_pipeline_kwargs.append(kwargs)
        return True

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_agent.run_rebase_conflict_resolution_pipeline",
        _fake_run_pipeline,
    )

    reset_fallback_warning_registry()

    warnings: list[str] = []
    sink_id = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        stop = RebaseStop(
            sha="abcdef1",
            subject="feat: conflict commit",
            conflicted_files=("src/main.py",),
            stop_index=1,
            stop_cap=5,
        )
        outcome = resolver(repo_root, "main", stop)
    finally:
        logger.remove(sink_id)

    assert outcome is True
    assert len(launched_pipeline_kwargs) == 1

    assert agents_file.read_bytes() == original_bytes

    warn_text = "\n".join(warnings)
    assert str(agents_file) in warn_text
    assert "using built-in default" in warn_text
    assert "ralph --check-config" in warn_text
    assert "ralph --regenerate-config" in warn_text
    assert "line" in warn_text.lower()
    assert "column" in warn_text.lower()

    target_config = launched_pipeline_kwargs[0]["config"]
    assert target_config.agents == {}
    assert launched_pipeline_kwargs[0]["root"] == repo_root


def test_corrupt_agents_toml_auto_integrate_rebase_does_not_enter_waiting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _seed_workspace(repo_root)

    _setup_corrupt_agents_toml(monkeypatch, tmp_path)

    policy = load_policy(default_dir())
    cfg = load_config(config_path=repo_root / ".agent" / "ralph-workflow.toml")
    reg = AgentRegistry.from_config(cfg)

    mock_display = MagicMock()
    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=policy,
        registry=reg,
        display=mock_display,
        config=cfg,
        pipeline_deps=MagicMock(),
        workspace_scope=WorkspaceScope(repo_root),
    )

    pipeline_invocations: list[dict[str, Any]] = []

    def _fake_run_pipeline(**kwargs: Any) -> bool:
        pipeline_invocations.append(kwargs)
        return True

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_agent.run_rebase_conflict_resolution_pipeline",
        _fake_run_pipeline,
    )

    def _fake_resolve_rebase(
        root: Path,
        target: str,
        res_fn: Any,
        session: Any = None,
    ) -> bool:
        del session
        stop = RebaseStop(
            sha="abcdef1",
            subject="feat: conflict commit",
            conflicted_files=("src/main.py",),
            stop_index=1,
            stop_cap=5,
        )
        return bool(res_fn(root, target, stop))

    mock_runner = MagicMock()
    mock_runner.resolve_rebase.side_effect = _fake_resolve_rebase

    monkeypatch.setattr(merge_mod, "set_resolving_rebase", MagicMock(return_value=True))
    monkeypatch.setattr(merge_mod, "read_record", MagicMock(return_value=None))

    run_result = resolve_conflicted_rebase(
        repo_root,
        "main",
        resolver,
        mock_display,
        conflict_resolution_config=None,
        agent_runner=mock_runner,
    )

    assert run_result is not None
    assert isinstance(run_result.rebase_outcome, RebaseSuccess)
    assert run_result.short_circuit is None
    assert len(pipeline_invocations) == 1


def test_corrupt_agents_toml_paused_rebase_in_run_loop_does_not_enter_waiting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _seed_workspace(repo_root)

    _setup_corrupt_agents_toml(monkeypatch, tmp_path)

    policy = load_policy(default_dir())
    cfg = load_config(config_path=repo_root / ".agent" / "ralph-workflow.toml")
    reg = AgentRegistry.from_config(cfg)

    ctx = MagicMock(spec=LoopContext)
    ctx.policy_bundle = policy
    ctx.registry = reg
    ctx.active_display = MagicMock()
    ctx.config = cfg
    ctx.pipeline_deps = MagicMock()
    ctx.workspace_scope = WorkspaceScope(repo_root)
    ctx.display_context = None

    pipeline_invocations: list[dict[str, Any]] = []

    def _fake_run_pipeline(**kwargs: Any) -> bool:
        pipeline_invocations.append(kwargs)
        return True

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_agent.run_rebase_conflict_resolution_pipeline",
        _fake_run_pipeline,
    )

    class _ContinuationExecutor:
        def continue_rebase(
            self,
            root: Path,
            target: str,
            resolver: Any,
            config: Any,
        ) -> tuple[bool, str | None, ResolutionFailureCause]:
            del config
            stop = RebaseStop(
                sha="abcdef1",
                subject="feat: conflict commit",
                conflicted_files=("src/main.py",),
                stop_index=1,
                stop_cap=5,
            )
            outcome = resolver(root, target, stop)
            if outcome is True:
                return (True, None, ResolutionFailureCause.NONE)
            return (False, "failed", ResolutionFailureCause.AGENT_FAILURE)

    resolved = resolve_paused_rebase(
        ctx,
        "main",
        executor=_ContinuationExecutor(),
    )

    assert resolved is True
    assert len(pipeline_invocations) == 1


def test_contrast_corrupt_ralph_workflow_toml_declines_and_enters_waiting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    agent_dir = repo_root / ".agent"
    agent_dir.mkdir(parents=True, exist_ok=True)
    bad_cfg = agent_dir / "ralph-workflow.toml"
    bad_cfg.write_text("corrupted = [to ml\n", encoding="utf-8")
    (repo_root / "PROMPT.md").write_text("prompt\n", encoding="utf-8")

    policy = load_policy(default_dir())
    mock_display = MagicMock()
    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=policy,
        registry=MagicMock(),
        display=mock_display,
        config=MagicMock(),
        pipeline_deps=MagicMock(),
        workspace_scope=WorkspaceScope(repo_root),
    )

    pipeline_invocations: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_agent.run_rebase_conflict_resolution_pipeline",
        lambda **kwargs: pipeline_invocations.append(kwargs) or True,
    )

    stop = RebaseStop(
        sha="abcdef1",
        subject="feat: conflict commit",
        conflicted_files=("src/main.py",),
        stop_index=1,
        stop_cap=5,
    )
    session = ResolutionSession()
    with bind_active_rebase_resolution_session(session):
        outcome = resolver(repo_root, "main", stop)

    assert not isinstance(outcome, bool)
    assert outcome.succeeded is False
    assert outcome.failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    assert len(pipeline_invocations) == 0
    assert session.terminal_reason == ResolutionTerminationReason.CONFIG_FAILURE

    def _fake_resolve_rebase(
        root: Path,
        target: str,
        res_fn: Any,
        session: Any = None,
    ) -> bool:
        if session is not None:
            with bind_active_rebase_resolution_session(session):
                res = res_fn(root, target, stop)
        else:
            res = res_fn(root, target, stop)
        return False if isinstance(res, bool) else res.succeeded

    mock_runner = MagicMock()
    mock_runner.resolve_rebase.side_effect = _fake_resolve_rebase
    monkeypatch.setattr(merge_mod, "set_resolving_rebase", MagicMock(return_value=True))
    monkeypatch.setattr(merge_mod, "read_record", MagicMock(return_value=None))

    run_result = resolve_conflicted_rebase(
        repo_root,
        "main",
        resolver,
        mock_display,
        conflict_resolution_config=None,
        agent_runner=mock_runner,
    )

    assert run_result is not None
    assert run_result.short_circuit is not None
    assert run_result.short_circuit.last_failure_cause == ResolutionFailureCause.CONFIG_FAILURE
    waiting_state = run_result.short_circuit.set_operation_display_state(
        OperationDisplayState.WAITING
    )
    assert waiting_state.operation_display_state == OperationDisplayState.WAITING


def test_corrupt_agents_toml_load_config_diagnose_and_runner_warn_with_recovery_instructions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _seed_workspace(repo_root)
    agents_file, _ = _setup_corrupt_agents_toml(
        monkeypatch,
        tmp_path,
        content="[agents]\ninvalid toml = = =\n",
    )

    warnings: list[str] = []
    sink_id = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        cfg = load_config(config_path=repo_root / ".agent" / "ralph-workflow.toml")
    finally:
        logger.remove(sink_id)

    assert cfg.agents == {}
    warn_text = "\n".join(warnings)
    assert str(agents_file) in warn_text
    assert "line 2" in warn_text
    assert "column" in warn_text
    assert "ralph --regenerate-config" in warn_text
    assert "using built-in default" in warn_text

    from ralph.config.bootstrap import collect_unreadable_config_files

    reset_fallback_warning_registry()
    warnings.clear()
    sink_id = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        findings = collect_unreadable_config_files(
            config_path=repo_root / ".agent" / "ralph-workflow.toml",
            workspace_scope=WorkspaceScope(repo_root),
        )
    finally:
        logger.remove(sink_id)

    assert len(findings) == 1
    assert findings[0].path == agents_file
    diag_warn = "\n".join(warnings)
    assert str(agents_file) in diag_warn
    assert "line 2" in diag_warn
    assert "column" in diag_warn
    assert "ralph --regenerate-config" in diag_warn

    reset_fallback_warning_registry()
    warnings.clear()
    policy = load_policy(default_dir())
    reg = AgentRegistry.from_config(cfg)
    mock_display = MagicMock()
    resolver = build_agent_rebase_stop_resolver(
        policy_bundle=policy,
        registry=reg,
        display=mock_display,
        config=cfg,
        pipeline_deps=MagicMock(),
        workspace_scope=WorkspaceScope(repo_root),
    )

    launched_kwargs: list[dict[str, Any]] = []

    def _fake_run_pipeline(**kwargs: Any) -> bool:
        launched_kwargs.append(kwargs)
        return True

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_agent.run_rebase_conflict_resolution_pipeline",
        _fake_run_pipeline,
    )

    sink_id = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        stop = RebaseStop(
            sha="abcdef1",
            subject="feat: conflict commit",
            conflicted_files=("src/main.py",),
            stop_index=1,
            stop_cap=5,
        )
        outcome = resolver(repo_root, "main", stop)
    finally:
        logger.remove(sink_id)

    assert outcome is True
    assert len(launched_kwargs) == 1
    assert launched_kwargs[0]["config"].agents == {}
    runner_warn = "\n".join(warnings)
    assert str(agents_file) in runner_warn
    assert "line 2" in runner_warn
    assert "column" in runner_warn
    assert "ralph --regenerate-config" in runner_warn


def test_corrupt_agents_toml_ordinary_pipeline_run_falls_back_without_crashing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    _seed_workspace(repo_root)
    agents_file, original_bytes = _setup_corrupt_agents_toml(
        monkeypatch,
        tmp_path,
        content="[agents]\ninvalid toml = = =\n",
    )
    scope = WorkspaceScope(repo_root)

    bundle = load_policy(default_dir())
    phases = dict(bundle.pipeline.phases)
    for name, phase_def in phases.items():
        if phase_def.invocation_gate is not None:
            phases[name] = phase_def.model_copy(update={"invocation_gate": None})
    planning_phase = phases["planning"]
    phases["planning"] = planning_phase.model_copy(
        update={
            "transitions": planning_phase.transitions.model_copy(update={"on_success": "complete"})
        }
    )
    bundle = bundle.model_copy(
        update={"pipeline": bundle.pipeline.model_copy(update={"phases": phases})}
    )

    invoked: list[tuple[str, str]] = []

    def fake_execute_effect(
        effect: Effect, _config: object, _scope: WorkspaceScope
    ) -> PipelineEvent:
        if isinstance(effect, InvokeAgentEffect):
            invoked.append((effect.agent_name, effect.phase))
            return PipelineEvent.AGENT_SUCCESS
        if isinstance(effect, CommitEffect):
            return PipelineEvent.COMMIT_SUCCESS
        raise AssertionError(f"Unexpected effect: {effect}")

    monkeypatch.setattr(runner, "resolve_workspace_scope", lambda: scope)
    monkeypatch.setattr(runner, "load_policy_or_die", lambda _p: bundle)
    monkeypatch.setattr(runner, "materialize_agent_prompt_if_needed", lambda *a, **kw: None)
    monkeypatch.setattr(runner, "materialize_prompt_for_phase", lambda *a, **kw: "noop")
    monkeypatch.setattr(runner, "execute_effect", fake_execute_effect)
    monkeypatch.setattr(
        runner, "phase_event_after_agent_run", lambda **kw: PipelineEvent.AGENT_SUCCESS
    )
    monkeypatch.setattr("ralph.pipeline.checkpoint.save", lambda *a, **kw: None)
    context = make_display_context(
        console=Console(record=True, force_terminal=False, color_system=None)
    )
    monkeypatch.setattr(runner, "make_display_context", lambda **kw: context)

    reset_fallback_warning_registry()
    warnings: list[str] = []
    sink_id = logger.add(warnings.append, level="WARNING", format="{message}")
    try:
        config = load_config(
            config_path=repo_root / ".agent" / "ralph-workflow.toml",
            workspace_scope=scope,
        )
        exit_code = pipeline_run(
            config,
            verbosity=Verbosity.QUIET,
            counter_overrides={"iteration": 1},
        )
    finally:
        logger.remove(sink_id)

    assert exit_code == 0
    assert invoked
    assert config.agents == {}
    assert agents_file.read_bytes() == original_bytes
    warning = "\n".join(warnings)
    assert str(agents_file) in warning
    assert "line 2" in warning
    assert "column" in warning
    assert "ralph --regenerate-config" in warning
    assert "using built-in default" in warning
