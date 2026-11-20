from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.system_clock import SystemClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.errors import GitOperationError
from ralph.git.operations import find_repo_root
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    seamed_dead_agent_probe_runner,
)
from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
from ralph.pipeline.auto_integrate_recovery import build_recovery_context
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.plumbing.commit_plumbing import attempt_controller, commit_probe_runner
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import (
    CANARY_MARKER_CONTENT,
    CANARY_MARKER_FILENAME,
    ProbeOutcome,
)
from ralph.session_runtime import (
    ManagedAgentSessionDeps,
    ManagedAgentSessionRequest,
    ManagedAgentSessionRuntime,
)
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.mcp.server.lifecycle import SessionBridgeLike
    from ralph.policy.models import PolicyBundle
    from ralph.recovery.controller import RecoveryController

_AGENT_NAME = "fake-agent"
_DEVELOPMENT_PHASE = "development"
_COMMIT_PHASE = "commit"


def _config() -> UnifiedConfig:
    return UnifiedConfig(
        agents={_AGENT_NAME: AgentConfig(cmd=_AGENT_NAME, model="fake-model")},
    )


def _failed_facts() -> AttemptFacts:
    return AttemptFacts(
        artifact_valid=False,
        workspace_changed=False,
        exit_status="exit_1",
        termination=TerminationRecord.create(
            TerminationIssuer.AGENT_EXIT,
            observed_returncode=1,
            reported_returncode=1,
        ),
    )


def _no_invoke(target: object, *args: object, **kwargs: object) -> object:
    del target, args, kwargs
    return PipelineEvent.AGENT_SUCCESS


def _deps() -> AttemptRunnerDeps:
    return AttemptRunnerDeps(
        clock=SystemClock(),
        invoke=_no_invoke,
        probe_runner=None,
        workspace_probe=None,
        connectivity=None,
    )


def _capture_probe_scratch(captured: list[Path]) -> Callable[..., Iterator[str]]:
    def _invoke(
        config: AgentConfig,
        prompt_file: str,
        *,
        options: InvokeOptions | None = None,
    ) -> Iterator[str]:
        assert config.cmd == _AGENT_NAME
        assert options is not None
        workspace_path = options.workspace_path
        assert workspace_path is not None
        captured.append(workspace_path)
        (workspace_path / CANARY_MARKER_FILENAME).write_text(
            CANARY_MARKER_CONTENT, encoding="utf-8"
        )
        return iter(())

    return _invoke


def _seed_foreign_repository(root: Path) -> Path:
    git_dir = root / ".git"
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "refs").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "config").write_text(
        "[core]\n\trepositoryformatversion = 0\n\tbare = false\n",
        encoding="utf-8",
    )
    return root


def _capture_session_runtime_probe_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> list[RecoveryController]:
    captured: list[RecoveryController] = []

    def _build(
        entry_point: AgentEntryPoint,
        *,
        config: UnifiedConfig,
        policy_bundle: PolicyBundle | None,
        deps: AttemptRunnerDeps,
        probe_trusted_parent: Path | None = None,
    ) -> RecoveryController:
        controller = build_entry_point_recovery_controller(
            entry_point,
            config=config,
            policy_bundle=policy_bundle,
            deps=replace(deps, probe_runner=None),
            probe_trusted_parent=probe_trusted_parent,
        )
        captured.append(controller)
        return controller

    monkeypatch.setattr(
        "ralph._session_runtime_runtime.build_entry_point_recovery_controller",
        _build,
    )
    return captured


def _fake_session_bridge() -> SessionBridgeLike:
    return cast(
        "SessionBridgeLike",
        SimpleNamespace(
            agent_endpoint_uri=lambda: "http://127.0.0.1:9999/mcp",
            shutdown=lambda: None,
        ),
    )


def test_the_attempt_runner_route_pre_trusts_the_scratch_outside_a_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_parent = tmp_path / "trusted-root"
    trusted_parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(GitOperationError):
        find_repo_root(outside)
    monkeypatch.chdir(outside)
    captured: list[Path] = []
    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _capture_probe_scratch(captured))

    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        config=_config(),
        policy_bundle=None,
        deps=_deps(),
        probe_trusted_parent=trusted_parent,
    )
    controller.finalize_attempt(
        _DEVELOPMENT_PHASE,
        _AGENT_NAME,
        facts=_failed_facts(),
        attempt_failed=True,
    )

    assert len(captured) == 1
    scratch = captured[0]
    assert scratch.parent == trusted_parent / ".agent" / "tmp"
    assert scratch.name.startswith("canary-probe-")


def test_the_attempt_runner_route_pre_trusts_the_scratch_inside_a_foreign_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_parent = tmp_path / "trusted-root"
    trusted_parent.mkdir()
    foreign = _seed_foreign_repository(tmp_path / "foreign")
    assert find_repo_root(foreign).resolve() == foreign.resolve()
    monkeypatch.chdir(foreign)
    captured: list[Path] = []
    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _capture_probe_scratch(captured))

    controller = build_entry_point_recovery_controller(
        AgentEntryPoint.MAIN_PHASE_LOOP,
        config=_config(),
        policy_bundle=None,
        deps=_deps(),
        probe_trusted_parent=trusted_parent,
    )
    controller.finalize_attempt(
        _DEVELOPMENT_PHASE,
        _AGENT_NAME,
        facts=_failed_facts(),
        attempt_failed=True,
    )

    assert len(captured) == 1
    scratch = captured[0]
    assert scratch.parent == trusted_parent / ".agent" / "tmp"
    assert scratch.name.startswith("canary-probe-")
    assert not scratch.is_relative_to(foreign.resolve())


def test_the_commit_probe_runner_pre_trusts_the_scratch_under_the_repo_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    captured: list[Path] = []
    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _capture_probe_scratch(captured))

    runner = commit_probe_runner(_config(), repo_root=repo_root)
    result = runner(_COMMIT_PHASE, _AGENT_NAME)

    assert result is not None
    assert result.outcome is ProbeOutcome.PASSED
    assert len(captured) == 1
    scratch = captured[0]
    assert scratch.parent == repo_root / ".agent" / "tmp"
    assert scratch.name.startswith("canary-probe-")


def test_the_commit_controller_pre_trusts_the_scratch_under_the_repo_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    captured: list[Path] = []
    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _capture_probe_scratch(captured))

    controller = attempt_controller(
        _config(),
        PipelineDeps(display_context=make_display_context()),
        _deps(),
        repo_root=repo_root,
    )
    controller.finalize_attempt(
        _COMMIT_PHASE,
        _AGENT_NAME,
        facts=_failed_facts(),
        attempt_failed=True,
    )

    assert len(captured) == 1
    scratch = captured[0]
    assert scratch.parent == repo_root / ".agent" / "tmp"
    assert scratch.name.startswith("canary-probe-")


def test_the_seamed_probe_runner_pre_trusts_the_scratch_under_the_given_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trusted_parent = tmp_path / "trusted-root"
    trusted_parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(GitOperationError):
        find_repo_root(outside)
    monkeypatch.chdir(outside)
    captured: list[Path] = []

    runner = seamed_dead_agent_probe_runner(
        _config(),
        _capture_probe_scratch(captured),
        trusted_parent=trusted_parent,
    )

    assert runner is not None
    result = runner(_DEVELOPMENT_PHASE, _AGENT_NAME)

    assert result is not None
    assert result.outcome is ProbeOutcome.PASSED
    assert len(captured) == 1
    scratch = captured[0]
    assert scratch.parent == trusted_parent / ".agent" / "tmp"
    assert scratch.name.startswith("canary-probe-")
    assert not scratch.is_relative_to(outside.resolve())

    foreign = _seed_foreign_repository(tmp_path / "foreign")
    assert find_repo_root(foreign).resolve() == foreign.resolve()
    monkeypatch.chdir(foreign)
    captured.clear()

    foreign_runner = seamed_dead_agent_probe_runner(
        _config(),
        _capture_probe_scratch(captured),
        trusted_parent=trusted_parent,
    )

    assert foreign_runner is not None
    foreign_result = foreign_runner(_DEVELOPMENT_PHASE, _AGENT_NAME)

    assert foreign_result is not None
    assert foreign_result.outcome is ProbeOutcome.PASSED
    assert len(captured) == 1
    foreign_scratch = captured[0]
    assert foreign_scratch.parent == trusted_parent / ".agent" / "tmp"
    assert foreign_scratch.name.startswith("canary-probe-")
    assert not foreign_scratch.is_relative_to(foreign.resolve())


def test_the_session_runtime_pre_trusts_the_probe_scratch_under_the_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(GitOperationError):
        find_repo_root(outside)
    monkeypatch.chdir(outside)
    captured: list[Path] = []
    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _capture_probe_scratch(captured))
    controllers = _capture_session_runtime_probe_controller(monkeypatch)
    config = _config()

    with ManagedAgentSessionRuntime.open(
        config=config,
        workspace_root=workspace_root,
        agent_config=config.agents[_AGENT_NAME],
        request=ManagedAgentSessionRequest(
            session_id_prefix="probe-trust",
            drain="standalone",
            capabilities=frozenset({"workspace.read"}),
            master_prompt_name=None,
        ),
        deps=ManagedAgentSessionDeps(
            start_mcp_server=lambda *_args: _fake_session_bridge(),
            invoke_agent=lambda *_args: iter(()),
            workspace_factory=lambda root: MemoryWorkspace(root=str(root)),
        ),
    ):
        assert len(controllers) == 1
        controllers[0].finalize_attempt(
            _DEVELOPMENT_PHASE,
            _AGENT_NAME,
            facts=_failed_facts(),
            attempt_failed=True,
        )

    assert len(captured) == 1
    scratch = captured[0]
    assert scratch.parent == workspace_root / ".agent" / "tmp"
    assert scratch.name.startswith("canary-probe-")
    assert not scratch.is_relative_to(outside.resolve())


def test_the_auto_integrate_recovery_pre_trusts_the_probe_scratch_under_the_workspace_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(GitOperationError):
        find_repo_root(outside)
    monkeypatch.chdir(outside)
    captured: list[Path] = []
    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _capture_probe_scratch(captured))
    scope = WorkspaceScope(root=workspace_root)

    _conflict_resolver, _rebase_resolver, controller = build_recovery_context(
        scope,
        _config(),
        display_context=make_display_context(),
    )

    assert controller is not None
    controller.finalize_attempt(
        _DEVELOPMENT_PHASE,
        _AGENT_NAME,
        facts=_failed_facts(),
        attempt_failed=True,
    )

    assert len(captured) == 1
    scratch = captured[0]
    assert scratch.parent == scope.root / ".agent" / "tmp"
    assert scratch.name.startswith("canary-probe-")
    assert not scratch.is_relative_to(outside.resolve())
