from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from ralph.agents.invoke import AttemptEnded
from ralph.agents.system_clock import SystemClock
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.git.errors import GitOperationError
from ralph.git.operations import find_repo_root
from ralph.pipeline import run_loop as run_loop_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline import standalone_integration_controller
from ralph.pipeline.agent_entry_point import AgentEntryPoint
from ralph.pipeline.attempt_runner import (
    build_entry_point_recovery_controller,
    seamed_dead_agent_probe_runner,
)
from ralph.pipeline.attempt_runner_deps import AttemptRunnerDeps
from ralph.pipeline.auto_integrate_recovery import build_recovery_context
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.factory import PipelineDeps
from ralph.pipeline.parallel import worker_runtime as worker_runtime_module
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.plumbing.commit_plumbing import attempt_controller, commit_probe_runner
from ralph.policy.loader import default_dir, load_policy
from ralph.project_policy import cli_integration as cli_integration_module
from ralph.recovery.attempt_facts import AttemptFacts, TerminationIssuer, TerminationRecord
from ralph.recovery.canary_probe import (
    CANARY_MARKER_CONTENT,
    CANARY_MARKER_FILENAME,
    ProbeOutcome,
)
from ralph.recovery.controller import RecoveryController
from ralph.recovery.dead_agent_probe import (
    DEFAULT_MAX_PROBES_PER_RUN,
    PROBE_REASON_BUDGET_EXHAUSTED,
)
from ralph.session_runtime import (
    ManagedAgentSessionDeps,
    ManagedAgentSessionRequest,
    ManagedAgentSessionRuntime,
)
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from ralph.agents.invoke import InvokeOptions
    from ralph.mcp.server.lifecycle import SessionBridgeLike
    from ralph.pipeline.resolution_outcome import StopResolutionOutcome
    from ralph.policy.models import PolicyBundle

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


def _default_policy_bundle() -> PolicyBundle:
    return load_policy(default_dir())


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


def _cwd_outside_any_repository(tmp_path: Path) -> Path:
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(GitOperationError):
        find_repo_root(outside)
    return outside


def _cwd_inside_foreign_repository(tmp_path: Path) -> Path:
    foreign = _seed_foreign_repository(tmp_path / "foreign")
    assert find_repo_root(foreign).resolve() == foreign.resolve()
    return foreign


def _failed_agent_effect(*_args: object, **_kwargs: object) -> AttemptEnded:
    return AttemptEnded(facts=_failed_facts())


def _stub_load_config(*_args: object, **_kwargs: object) -> UnifiedConfig:
    return _config()


def _stub_determine_effect_from_policy(*_args: object, **_kwargs: object) -> InvokeAgentEffect:
    return InvokeAgentEffect(
        agent_name=_AGENT_NAME,
        phase=_DEVELOPMENT_PHASE,
        prompt_file="ignored.md",
        drain=_DEVELOPMENT_PHASE,
    )


def _stub_render_phase_failure_report(*_args: object, **_kwargs: object) -> None:
    return None


def _capture_probe_invocations(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    captured: list[Path] = []
    monkeypatch.setattr(
        "ralph.agents.invoke.invoke_agent",
        _capture_probe_scratch(captured),
    )
    return captured


def _assert_probe_scratch_under(captured: list[Path], workspace_root: Path) -> Path:
    assert len(captured) == 1
    scratch = captured[0]
    assert scratch.parent == workspace_root / ".agent" / "tmp"
    assert scratch.name.startswith("canary-probe-")
    return scratch


def _unused_conflict_resolver(_root: Path, _prompt: str) -> StopResolutionOutcome:
    raise AssertionError("the conflict resolver must not run in the probe-trust route test")


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


def test_the_commit_probe_callback_spends_one_shared_per_run_probe_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured: list[Path] = []
    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _capture_probe_scratch(captured))

    runner = commit_probe_runner(_config(), repo_root=repo_root)
    results = [runner(_COMMIT_PHASE, _AGENT_NAME) for _ in range(DEFAULT_MAX_PROBES_PER_RUN + 1)]

    assert len(captured) == DEFAULT_MAX_PROBES_PER_RUN
    assert all(result is not None for result in results[:DEFAULT_MAX_PROBES_PER_RUN])
    assert [result.outcome for result in results[:DEFAULT_MAX_PROBES_PER_RUN] if result] == [
        ProbeOutcome.PASSED
    ] * DEFAULT_MAX_PROBES_PER_RUN
    spent = results[-1]
    assert spent is not None
    assert spent.outcome is ProbeOutcome.INCONCLUSIVE
    assert spent.reason == PROBE_REASON_BUDGET_EXHAUSTED


def test_the_commit_probe_budget_is_owned_by_the_callback_and_not_shared_globally(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    captured: list[Path] = []
    monkeypatch.setattr("ralph.agents.invoke.invoke_agent", _capture_probe_scratch(captured))

    first = commit_probe_runner(_config(), repo_root=repo_root)
    second = commit_probe_runner(_config(), repo_root=repo_root)
    for _ in range(DEFAULT_MAX_PROBES_PER_RUN):
        assert first(_COMMIT_PHASE, _AGENT_NAME) is not None
    assert len(captured) == DEFAULT_MAX_PROBES_PER_RUN

    resumed = second(_COMMIT_PHASE, _AGENT_NAME)

    assert resumed is not None
    assert resumed.outcome is ProbeOutcome.PASSED
    assert len(captured) == DEFAULT_MAX_PROBES_PER_RUN + 1


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


def _run_main_phase_loop_route(
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
) -> None:
    monkeypatch.setattr(runner_module, "execute_agent_effect", _failed_agent_effect)
    runner_module.execute_effect(
        InvokeAgentEffect(
            agent_name=_AGENT_NAME,
            phase=_DEVELOPMENT_PHASE,
            prompt_file="prompt.md",
            drain=_DEVELOPMENT_PHASE,
            chain_name=_DEVELOPMENT_PHASE,
        ),
        _config(),
        WorkspaceScope(root=workspace_root),
        pipeline_deps=make_test_pipeline_deps(make_display_context()),
    )


def test_the_main_phase_loop_route_pre_trusts_the_scratch_outside_a_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = _cwd_outside_any_repository(tmp_path)
    monkeypatch.chdir(outside)
    captured = _capture_probe_invocations(monkeypatch)

    _run_main_phase_loop_route(monkeypatch, workspace_root)

    scratch = _assert_probe_scratch_under(captured, workspace_root)
    assert not scratch.is_relative_to(outside.resolve())


def test_the_main_phase_loop_route_pre_trusts_the_scratch_inside_a_foreign_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    foreign = _cwd_inside_foreign_repository(tmp_path)
    monkeypatch.chdir(foreign)
    captured = _capture_probe_invocations(monkeypatch)

    _run_main_phase_loop_route(monkeypatch, workspace_root)

    scratch = _assert_probe_scratch_under(captured, workspace_root)
    assert not scratch.is_relative_to(foreign.resolve())


def _run_preamble_route(
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
) -> RecoveryController:
    handed: list[RecoveryController] = []

    def _fake_recover_incomplete_integration(*_args: object, **kwargs: object) -> None:
        controller = kwargs.get("recovery_controller")
        assert isinstance(controller, RecoveryController)
        handed.append(controller)

    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate.recover_incomplete_integration",
        _fake_recover_incomplete_integration,
    )
    run_loop_module.run_auto_integrate_recovery_preamble(
        WorkspaceScope(root=workspace_root),
        _config(),
        conflict_resolver=_unused_conflict_resolver,
    )
    assert len(handed) == 1
    return handed[0]


def test_the_run_loop_preamble_route_pre_trusts_the_scratch_outside_a_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = _cwd_outside_any_repository(tmp_path)
    monkeypatch.chdir(outside)
    captured = _capture_probe_invocations(monkeypatch)

    controller = _run_preamble_route(monkeypatch, workspace_root)
    controller.finalize_attempt(
        _DEVELOPMENT_PHASE,
        _AGENT_NAME,
        facts=_failed_facts(),
        attempt_failed=True,
    )

    scratch = _assert_probe_scratch_under(captured, workspace_root)
    assert not scratch.is_relative_to(outside.resolve())


def test_the_run_loop_preamble_route_pre_trusts_the_scratch_inside_a_foreign_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    foreign = _cwd_inside_foreign_repository(tmp_path)
    monkeypatch.chdir(foreign)
    captured = _capture_probe_invocations(monkeypatch)

    controller = _run_preamble_route(monkeypatch, workspace_root)
    controller.finalize_attempt(
        _DEVELOPMENT_PHASE,
        _AGENT_NAME,
        facts=_failed_facts(),
        attempt_failed=True,
    )

    scratch = _assert_probe_scratch_under(captured, workspace_root)
    assert not scratch.is_relative_to(foreign.resolve())


def test_the_standalone_integration_controller_route_pre_trusts_the_scratch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    foreign = _cwd_inside_foreign_repository(tmp_path)
    monkeypatch.chdir(foreign)
    captured = _capture_probe_invocations(monkeypatch)

    controller = standalone_integration_controller(workspace_root, _config())
    controller.finalize_attempt(
        _DEVELOPMENT_PHASE,
        _AGENT_NAME,
        facts=_failed_facts(),
        attempt_failed=True,
    )

    scratch = _assert_probe_scratch_under(captured, workspace_root)
    assert not scratch.is_relative_to(foreign.resolve())


def _run_project_policy_cli_route(
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
) -> RecoveryController:
    built: list[RecoveryController] = []

    def _builder(
        entry_point: AgentEntryPoint,
        *,
        config: UnifiedConfig | None,
        policy_bundle: PolicyBundle | None,
        deps: AttemptRunnerDeps,
        probe_trusted_parent: Path | None = None,
    ) -> RecoveryController:
        controller = build_entry_point_recovery_controller(
            entry_point,
            config=config,
            policy_bundle=policy_bundle,
            deps=deps,
            probe_trusted_parent=probe_trusted_parent,
        )
        built.append(controller)
        return controller

    monkeypatch.setattr(
        cli_integration_module,
        "build_entry_point_recovery_controller",
        _builder,
    )
    cli_integration_module.make_production_invoke_agent(
        cli_integration_module.LoadResult(
            config=_config(),
            workspace_scope=WorkspaceScope(root=workspace_root),
            initial_state=None,
            policy_bundle=None,
            run_id="probe-trust",
        ),
        None,
        WorkspaceScope(root=workspace_root),
        None,
        make_display_context(),
    )
    assert len(built) == 1
    return built[0]


def test_the_project_policy_cli_route_pre_trusts_the_scratch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    foreign = _cwd_inside_foreign_repository(tmp_path)
    monkeypatch.chdir(foreign)
    captured = _capture_probe_invocations(monkeypatch)

    controller = _run_project_policy_cli_route(monkeypatch, workspace_root)
    controller.finalize_attempt(
        _DEVELOPMENT_PHASE,
        _AGENT_NAME,
        facts=_failed_facts(),
        attempt_failed=True,
    )

    scratch = _assert_probe_scratch_under(captured, workspace_root)
    assert not scratch.is_relative_to(foreign.resolve())


def _run_parallel_worker_route(
    monkeypatch: pytest.MonkeyPatch,
    workspace_root: Path,
) -> None:
    worker_namespace = workspace_root / ".agent" / "workers" / "unit-a"
    worker_namespace.mkdir(parents=True)
    manifest = ParallelWorkerManifest(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
        phase=_DEVELOPMENT_PHASE,
        drain=_DEVELOPMENT_PHASE,
        config_path=str(workspace_root / "configs" / "worker.toml"),
        cli_overrides={},
        worker_namespace=str(worker_namespace),
        worker_artifact_dir=str(worker_namespace / "artifacts"),
        prompt_file=str(worker_namespace / "tmp" / "development_prompt.md"),
        workspace_root=str(workspace_root),
    )
    manifest_path = workspace_root / "worker-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    monkeypatch.setattr(
        worker_runtime_module,
        "load_config",
        _stub_load_config,
        raising=False,
    )
    monkeypatch.setattr(
        worker_runtime_module,
        "determine_effect_from_policy",
        _stub_determine_effect_from_policy,
        raising=False,
    )
    monkeypatch.setattr(
        worker_runtime_module,
        "execute_agent_effect",
        _failed_agent_effect,
        raising=False,
    )
    monkeypatch.setattr(
        worker_runtime_module,
        "render_phase_failure_report",
        _stub_render_phase_failure_report,
        raising=False,
    )

    worker_runtime_module.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=make_display_context(),
        pipeline_deps=make_test_pipeline_deps(make_display_context()),
    )


def test_the_parallel_worker_route_pre_trusts_the_scratch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    foreign = _cwd_inside_foreign_repository(tmp_path)
    monkeypatch.chdir(foreign)
    captured = _capture_probe_invocations(monkeypatch)

    _run_parallel_worker_route(monkeypatch, workspace_root)

    scratch = _assert_probe_scratch_under(captured, workspace_root)
    assert not scratch.is_relative_to(foreign.resolve())
