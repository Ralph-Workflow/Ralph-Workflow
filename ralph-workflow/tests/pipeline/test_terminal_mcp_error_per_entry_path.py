from __future__ import annotations

import importlib
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger as loguru_logger

from ralph.agents.invoke import AgentInvocationError, InvokeOptions
from ralph.cli.commands._commit_attempt_context import CommitAttemptContext
from ralph.cli.commands._load_result import _LoadResult
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import make_display_context
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline import runner as runner_module
from ralph.pipeline.conflict_resolution.session import ResolutionSession, invoke_resolution_agent
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent, PipelineEvent
from ralph.pipeline.parallel import worker_runtime
from ralph.pipeline.parallel.worker_manifest import ParallelWorkerManifest
from ralph.pipeline.phase_agent_handler import phase_event_after_agent_run
from ralph.pipeline.plumbing import commit_plumbing, smoke_plumbing
from ralph.pipeline.runner import load_policy_bundle_for_run
from ralph.pipeline.state import PipelineState
from ralph.project_policy import cli_integration
from ralph.project_policy.pipeline_graph import PHASE_REMEDIATION
from ralph.workspace import FsWorkspace, WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.pipeline._terminal_mcp_error_seams import (
    _AGENT_PROCESS_LABEL_PREFIX,
    _assert_no_traceback_or_crash,
    _default_policy_bundle,
    _deps_with_terminal_supervisor,
    _fake_materialize_master_prompt,
    _fast_heartbeat,
    _HealthyBridge,
    _log_capture,
    _no_agent_output,
    _no_bridge_health_check,
    _patch_invoke_agent,
    _policy_bundle,
    _RegistryForAnyAgentFactory,
    _SequenceBridgeFactory,
    _TerminalBridge,
    _TerminalSupervisorFactory,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.phases.phase_context import PhaseContext
    from ralph.pipeline.effects import Effect
    from ralph.pipeline.events import Event
    from ralph.pipeline.factory import PipelineDeps
    from ralph.policy.models import PolicyBundle


class _FakeBridge(RestartAwareMcpBridge):
    def __init__(self) -> None:
        pass

    @property
    def run_id(self) -> str:
        return "test-run-id"

    def start(self) -> None:
        pass

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"

    def endpoint_uri(self) -> str:
        return self.agent_endpoint_uri()

    def shutdown(self) -> None:
        pass


def _registry_factory(config: object) -> object:
    del config
    agent_config = AgentConfig(cmd="opencode", output_flag="--json-stream")

    class _Registry:
        def get(self, name: str) -> AgentConfig | None:
            del name
            return agent_config

    return _Registry()


def _make_deps(tmp_path: Path) -> PipelineDeps:
    return make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge=_FakeBridge(),
        master_prompt_materializer=_fake_materialize_master_prompt(tmp_path),
        registry_factory=_registry_factory,
        check_mcp_bridge_health_fn=_no_bridge_health_check,
    )


def test_mcp_server_error_converts_to_agent_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    config = UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    deps = _make_deps(tmp_path)
    fake_bridge = _FakeBridge()

    def fake_run_with_direct_mcp_recovery(*args: object, **kwargs: object) -> object:
        raise McpServerError(
            "MCP server failed",
            restart_count=3,
            last_stage="registry_built",
            elapsed_s=12.5,
            restart_reason="preflight_timeout",
            handover_step="tools/call",
            probe_duration_s=10.0,
            probe_timeout_s=30.0,
            child_pid=12345,
            psutil_status="terminated",
            failure_kind="transient",
        )

    monkeypatch.setattr(
        effect_executor_module,
        "run_with_direct_mcp_recovery",
        fake_run_with_direct_mcp_recovery,
    )

    logged_all: list[str] = []
    sink_all = loguru_logger.add(
        logged_all.append,
        level="DEBUG",
        format="{message}",
    )

    try:
        result = effect_executor_module.execute_agent_effect(
            effect,
            config,
            deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            invoke_agent=_no_agent_output,
            agent_invocation_error=AgentInvocationError,
            bridge=fake_bridge,
        )

        assert result == PipelineEvent.AGENT_FAILURE
        assert not any("Phase handler crashed" in m for m in logged_all)
    finally:
        loguru_logger.remove(sink_all)


def test_mcp_server_error_logs_last_stage_at_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    config = UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    deps = _make_deps(tmp_path)
    fake_bridge = _FakeBridge()

    logged_warnings: list[str] = []
    sink_id = loguru_logger.add(
        logged_warnings.append,
        level="WARNING",
        format="{message}",
    )

    def fake_run_with_direct_mcp_recovery(*args: object, **kwargs: object) -> object:
        raise McpServerError(
            "MCP server failed",
            restart_count=3,
            last_stage="registry_built",
            elapsed_s=12.5,
            restart_reason="preflight_timeout",
            handover_step="tools/call",
            probe_duration_s=10.0,
            probe_timeout_s=30.0,
            child_pid=12345,
            psutil_status="terminated",
            failure_kind="transient",
        )

    monkeypatch.setattr(
        effect_executor_module,
        "run_with_direct_mcp_recovery",
        fake_run_with_direct_mcp_recovery,
    )

    try:
        result = effect_executor_module.execute_agent_effect(
            effect,
            config,
            deps,
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            invoke_agent=_no_agent_output,
            agent_invocation_error=AgentInvocationError,
            bridge=fake_bridge,
        )

        assert result == PipelineEvent.AGENT_FAILURE
        warning_logs = [m for m in logged_warnings if "last_stage=registry_built" in m]
        assert len(warning_logs) >= 1, (
            f"Expected 'last_stage=registry_built' in WARNING logs, got: {logged_warnings}"
        )
    finally:
        loguru_logger.remove(sink_id)


def test_phase_handler_does_not_log_mcp_server_error_as_traceback() -> None:
    root = Path(__file__).parent.parent.parent / "ralph"
    import re

    pattern = re.compile(r'logger\.exception.*"Phase handler crashed')
    matches: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if pattern.search(text):
            matches.append(str(path))
    assert not matches, f"logger.exception('Phase handler crashed') still appears in: {matches}"


def test_phase_agent_handler_catches_mcp_server_error_without_crashed_log(
    tmp_path: Path,
) -> None:
    config = UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))
    effect = InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md")
    workspace = FsWorkspace(tmp_path)
    policy_bundle = load_policy_bundle_for_run(WorkspaceScope(tmp_path), config)

    logged_messages: list[str] = []
    sink_id = loguru_logger.add(
        logged_messages.append,
        level="DEBUG",
        format="{message}",
    )

    def fake_handle_phase(effect: Effect, ctx: PhaseContext) -> list[Event]:
        del effect, ctx
        raise McpServerError(
            "server terminated",
            restart_count=2,
            last_stage="readiness_probe",
            restart_reason="probe_timeout",
            failure_kind="transient",
        )

    try:
        event = phase_event_after_agent_run(
            effect=effect,
            config=config,
            policy_bundle=policy_bundle,
            workspace=workspace,
            display_context=make_display_context(),
            handle_phase_fn=fake_handle_phase,
        )
        assert isinstance(event, PhaseFailureEvent)
        assert event.phase == "development"
        assert not any("Phase handler crashed" in msg for msg in logged_messages), (
            f"Found 'Phase handler crashed' in logs: {logged_messages}"
        )
        warning_logs = [msg for msg in logged_messages if "Phase handler McpServerError" in msg]
        assert len(warning_logs) >= 1, f"Expected warning log, got: {logged_messages}"
    finally:
        loguru_logger.remove(sink_id)


@dataclass(frozen=True)
class _RunnerOutcome:
    event: Event


@dataclass(frozen=True)
class _WorkerOutcome:
    exit_code: int
    failure_report_rendered: bool
    success_path_taken: bool


@dataclass(frozen=True)
class _ConflictOutcome:
    launched: bool
    last_attempt_evidence: str | None


@dataclass(frozen=True)
class _CommitOutcome:
    failure_detail: str | None
    last_error: BaseException | None


@dataclass(frozen=True)
class _SmokeOutcome:
    final_exception: AgentInvocationError | None
    supervised_turns: int
    turns_completed: int = 1


@dataclass(frozen=True)
class _PolicyOutcome:
    launched: bool


@dataclass(frozen=True)
class _EntryPath:
    path_id: str
    call_site: str
    drive: Callable[[pytest.MonkeyPatch, Path, PipelineDeps, _TerminalSupervisorFactory], object]
    assert_public_outcome: Callable[[object], None]
    retry_intent_parked: bool


def _drive_runner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deps: PipelineDeps,
    factory: _TerminalSupervisorFactory,
) -> object:
    del monkeypatch, factory
    return _RunnerOutcome(
        runner_module._execute_effect(
            InvokeAgentEffect(agent_name="dev", phase="development", prompt_file="dev.md"),
            UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0)),
            WorkspaceScope(tmp_path),
            display_context=make_display_context(),
            pipeline_deps=deps,
            policy_bundle=_policy_bundle(tmp_path),
        )
    )


def _assert_runner_outcome(outcome: object) -> None:
    assert isinstance(outcome, _RunnerOutcome)
    assert outcome.event == PipelineEvent.AGENT_FAILURE


def _drive_worker_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deps: PipelineDeps,
    factory: _TerminalSupervisorFactory,
) -> object:
    del factory
    worker_ns = tmp_path / ".agent" / "workers" / "unit-a"
    worker_ns.mkdir(parents=True)
    manifest = ParallelWorkerManifest(
        unit_id="unit-a",
        description="Implement only unit A",
        allowed_directories=["src/a"],
        phase="development",
        drain="development",
        config_path=str(tmp_path / "configs" / "worker.toml"),
        cli_overrides={"agent": "opencode", "verbose": True},
        worker_namespace=str(worker_ns),
        worker_artifact_dir=str(worker_ns / "artifacts"),
        prompt_file=str(worker_ns / "tmp" / "development_prompt.md"),
        workspace_root=str(tmp_path),
    )
    manifest_path = tmp_path / "worker-manifest.json"
    manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    rendered: list[bool] = []
    succeeded: list[bool] = []

    class _FakeWorkspace:
        def __init__(self, root: Path, *, allowed_roots: tuple[Path, ...] | None = None) -> None:
            del root, allowed_roots

        def read(self, path: str) -> str:
            del path
            return "worker prompt body"

    def _load_config(*args: object, **kwargs: object) -> UnifiedConfig:
        del args, kwargs
        return UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0))

    def _load_policy(*args: object, **kwargs: object) -> PolicyBundle:
        del args, kwargs
        return _policy_bundle(tmp_path)

    def _initial_state(*args: object, **kwargs: object) -> PipelineState:
        del args, kwargs
        return PipelineState(phase="development")

    def _effect_for_phase(*args: object, **kwargs: object) -> InvokeAgentEffect:
        del args, kwargs
        return InvokeAgentEffect(
            agent_name="developer",
            phase="development",
            prompt_file="ignored.md",
            drain="development",
        )

    def _render_failure_report(*args: object, **kwargs: object) -> None:
        del args, kwargs
        rendered.append(True)

    def _phase_event_after_agent_run(*args: object, **kwargs: object) -> PipelineEvent:
        del args, kwargs
        succeeded.append(True)
        return PipelineEvent.AGENT_SUCCESS

    monkeypatch.setattr(worker_runtime, "load_config", _load_config, raising=False)
    monkeypatch.setattr(
        worker_runtime, "load_policy_for_workspace_scope", _load_policy, raising=False
    )
    monkeypatch.setattr(worker_runtime, "create_initial_state", _initial_state, raising=False)
    monkeypatch.setattr(
        worker_runtime, "determine_effect_from_policy", _effect_for_phase, raising=False
    )
    monkeypatch.setattr(worker_runtime, "FsWorkspace", _FakeWorkspace, raising=False)
    monkeypatch.setattr(
        worker_runtime, "render_phase_failure_report", _render_failure_report, raising=False
    )
    monkeypatch.setattr(
        worker_runtime, "phase_event_after_agent_run", _phase_event_after_agent_run, raising=False
    )
    exit_code = worker_runtime.run_parallel_worker_from_manifest(
        manifest_path=manifest_path,
        display_context=make_display_context(),
        pipeline_deps=deps,
    )
    return _WorkerOutcome(
        exit_code=exit_code,
        failure_report_rendered=bool(rendered),
        success_path_taken=bool(succeeded),
    )


def _assert_worker_outcome(outcome: object) -> None:
    assert isinstance(outcome, _WorkerOutcome)
    assert outcome.exit_code == 1
    assert outcome.failure_report_rendered is True
    assert outcome.success_path_taken is False


def _drive_conflict_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deps: PipelineDeps,
    factory: _TerminalSupervisorFactory,
) -> object:
    del monkeypatch, factory
    prompt_path = tmp_path / "resolution_prompt.md"
    prompt_path.write_text("resolve", encoding="utf-8")
    session = ResolutionSession()
    launched = invoke_resolution_agent(
        agent_name="resolver",
        prompt_path=prompt_path,
        config=UnifiedConfig.model_validate({"general": {"agent_idle_timeout_seconds": 300.0}}),
        pipeline_deps=deps,
        workspace_scope=WorkspaceScope(tmp_path),
        policy_bundle=_policy_bundle(tmp_path),
        display=None,
        display_context=make_display_context(),
        session=session,
    )
    return _ConflictOutcome(launched=launched, last_attempt_evidence=session.last_attempt_evidence)


def _assert_conflict_outcome(outcome: object) -> None:
    assert isinstance(outcome, _ConflictOutcome)
    assert outcome.launched is False
    assert outcome.last_attempt_evidence == "McpServerError"


def _commit_attempt_with_bridge(
    tmp_path: Path,
    deps: PipelineDeps,
    bridge: RestartAwareMcpBridge,
) -> _CommitOutcome:
    importlib.import_module("ralph.cli.commands.commit")
    from ralph.pipeline.plumbing import commit_plumbing

    prompt_path = tmp_path / "PROMPT.md"
    if not prompt_path.exists():
        prompt_path.write_text("commit prompt", encoding="utf-8")
    attempt, _session_id, last_error = commit_plumbing._run_commit_agent_attempt_with_recovery(
        "claude",
        AgentConfig(cmd="claude", transport="claude", json_parser="generic"),
        prompt_file=str(prompt_path),
        attempt_context=CommitAttemptContext(
            repo_root=tmp_path,
            verbose=False,
            extra_env={},
            general_config=None,
            bridge=bridge,
        ),
        display_context=make_display_context(),
        max_retries=1,
        pipeline_deps=deps,
    )
    return _CommitOutcome(failure_detail=attempt.failure_detail, last_error=last_error)


def _drive_commit_plumbing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deps: PipelineDeps,
    factory: _TerminalSupervisorFactory,
) -> object:
    del monkeypatch, factory
    return _commit_attempt_with_bridge(tmp_path, deps, _TerminalBridge())


def _assert_commit_outcome(outcome: object) -> None:
    assert isinstance(outcome, _CommitOutcome)
    assert outcome.failure_detail, "a failed commit attempt must carry a failure_detail"
    assert isinstance(outcome.last_error, McpServerError)


def _smoke_turn_with_bridge(
    tmp_path: Path,
    deps: PipelineDeps,
    bridge: RestartAwareMcpBridge,
    factory: _TerminalSupervisorFactory,
    *,
    first_turn: bool,
) -> _SmokeOutcome:
    from ralph.pipeline.plumbing import smoke_plumbing
    from ralph.pipeline.plumbing.smoke_run_params import SmokeRunParams

    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("smoke prompt", encoding="utf-8")
    params = SmokeRunParams(
        agent_name="opencode/test-model",
        config=AgentConfig(cmd="opencode", output_flag="--json-stream"),
        unified_config=UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0)),
        workspace_root=tmp_path,
        prompt_file=prompt_file,
        output_file=tmp_path / "tmp" / "smoke" / "todo-list.js",
        options=InvokeOptions(),
        display_context=make_display_context(),
        bridge=bridge,
        pipeline_deps=deps,
    )
    supervised_before = len(factory.supervisors)
    _all_lines, _live, _session_id, final_exception, _ceiling = smoke_plumbing._execute_smoke_turns(
        params, None, run_id="terminal-mcp-error-smoke"
    )
    return _SmokeOutcome(
        final_exception=final_exception,
        supervised_turns=len(factory.supervisors) - supervised_before,
        turns_completed=1 if first_turn else 2,
    )


def _drive_smoke_plumbing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deps: PipelineDeps,
    factory: _TerminalSupervisorFactory,
) -> object:
    del monkeypatch
    return _smoke_turn_with_bridge(tmp_path, deps, _TerminalBridge(), factory, first_turn=True)


def _assert_smoke_outcome(outcome: object) -> None:
    assert isinstance(outcome, _SmokeOutcome)
    assert outcome.final_exception is None
    assert outcome.supervised_turns == 1


def _drive_policy_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    deps: PipelineDeps,
    factory: _TerminalSupervisorFactory,
) -> object:
    del monkeypatch, factory
    workspace_scope = WorkspaceScope(tmp_path)
    load_result = _LoadResult(
        config=UnifiedConfig(general=GeneralConfig(agent_idle_timeout_seconds=300.0)),
        workspace_scope=workspace_scope,
        initial_state=PipelineState(phase="planning", policy_entry_phase="planning"),
        policy_bundle=_default_policy_bundle(),
        run_id="terminal-mcp-error-policy",
    )
    invoke = cli_integration._make_production_invoke_agent(
        load_result,
        deps,
        workspace_scope,
        None,
        make_display_context(),
    )
    return _PolicyOutcome(
        launched=invoke(phase=PHASE_REMEDIATION, prompt_path=str(tmp_path / "PROMPT.md"))
    )


def _assert_policy_outcome(outcome: object) -> None:
    assert isinstance(outcome, _PolicyOutcome)
    assert outcome.launched is False


_ENTRY_PATHS: tuple[_EntryPath, ...] = (
    _EntryPath(
        "runner",
        "ralph/pipeline/runner.py:399",
        _drive_runner,
        _assert_runner_outcome,
        True,
    ),
    _EntryPath(
        "worker_runtime",
        "ralph/pipeline/parallel/worker_runtime.py:514",
        _drive_worker_runtime,
        _assert_worker_outcome,
        True,
    ),
    _EntryPath(
        "conflict_session",
        "ralph/pipeline/conflict_resolution/session.py:363",
        _drive_conflict_session,
        _assert_conflict_outcome,
        False,
    ),
    _EntryPath(
        "commit_plumbing",
        "ralph/pipeline/plumbing/commit_plumbing.py:667",
        _drive_commit_plumbing,
        _assert_commit_outcome,
        True,
    ),
    _EntryPath(
        "smoke_plumbing",
        "ralph/pipeline/plumbing/smoke_plumbing.py:1514",
        _drive_smoke_plumbing,
        _assert_smoke_outcome,
        True,
    ),
    _EntryPath(
        "policy_cli",
        "ralph/project_policy/cli_integration.py:403",
        _drive_policy_cli,
        _assert_policy_outcome,
        False,
    ),
)


@pytest.mark.parametrize(
    ("entry_path"),
    _ENTRY_PATHS,
    ids=[f"{path.path_id}" for path in _ENTRY_PATHS],
)
def test_terminal_mcp_error_converts_to_typed_failure_on_every_entry_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    entry_path: _EntryPath,
) -> None:
    deps, factory = _deps_with_terminal_supervisor(tmp_path)
    _patch_invoke_agent(monkeypatch, factory.agent_exits_cleanly)

    with _log_capture() as recorder:
        outcome = entry_path.drive(monkeypatch, tmp_path, deps, factory)

    entry_path.assert_public_outcome(outcome)
    _assert_no_traceback_or_crash(recorder)

    assert len(factory.supervisors) >= 1
    assert factory.terminal.is_set()
    assert all(scope for scope in factory.label_scopes)
    assert list(factory.interrupted_labels) == [
        f"{_AGENT_PROCESS_LABEL_PREFIX}{scope}:" for scope in factory.label_scopes
    ]

    intent = effect_executor_module.pop_last_captured_retry_intent()
    if entry_path.retry_intent_parked:
        assert intent.failure_reason == "McpServerError"
        assert intent.action is None
    elif entry_path.path_id == "conflict_session":
        assert intent.failure_reason in {"", None}
    else:
        assert intent.failure_reason in {"", None}, (
            f"unexpected leftover retry intent on {entry_path.call_site}: "
            f"failure_reason={intent.failure_reason!r}"
        )


def _drive_bridge_forwarding_path(
    tmp_path: Path,
    deps: PipelineDeps,
    bridge: RestartAwareMcpBridge,
    factory: _TerminalSupervisorFactory,
    path_id: str,
    turn_index: int,
) -> object:
    if path_id == "commit_plumbing":
        return _commit_attempt_with_bridge(tmp_path, deps, bridge)
    return _smoke_turn_with_bridge(tmp_path, deps, bridge, factory, first_turn=turn_index == 1)


_BRIDGE_FORWARDING_PATHS: tuple[str, ...] = ("commit_plumbing", "smoke_plumbing")


def _patch_execute_agent_effect_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> list[PipelineEvent]:
    captured: list[PipelineEvent] = []
    real_execute = effect_executor_module.execute_agent_effect

    def recording_execute(*args: object, **kwargs: object) -> PipelineEvent:
        result = real_execute(*args, **kwargs)
        captured.append(result)
        return result

    for module in (commit_plumbing, smoke_plumbing):
        monkeypatch.setattr(module, "execute_agent_effect", recording_execute, raising=False)
    return captured


@pytest.mark.parametrize("path_id", _BRIDGE_FORWARDING_PATHS)
def test_terminal_failure_then_distinct_healthy_bridge_succeeds_on_next_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path_id: str,
) -> None:
    dead_bridge = _TerminalBridge()
    healthy_bridge = _HealthyBridge()
    bridge_factory = _SequenceBridgeFactory(deque([healthy_bridge]))

    factory = _TerminalSupervisorFactory()
    deps = make_test_pipeline_deps(
        display_context=make_display_context(),
        bridge_factory=bridge_factory,
        master_prompt_materializer=_fake_materialize_master_prompt(tmp_path),
        registry_factory=_RegistryForAnyAgentFactory(),
        check_mcp_bridge_health_fn=_no_bridge_health_check,
        mcp_supervisor_factory=factory,
        heartbeat_policy_from_env_fn=_fast_heartbeat,
    )

    _patch_invoke_agent(monkeypatch, factory.attempt_returns_immediately)
    captured_events = _patch_execute_agent_effect_capture(monkeypatch)

    with _log_capture() as recorder:
        first = _drive_bridge_forwarding_path(tmp_path, deps, dead_bridge, factory, path_id, 1)
        assert isinstance(first, _CommitOutcome | _SmokeOutcome)
        if isinstance(first, _SmokeOutcome):
            assert first.supervised_turns == 1
            assert first.turns_completed == 1

        second = _drive_bridge_forwarding_path(tmp_path, deps, dead_bridge, factory, path_id, 2)
        assert isinstance(second, _CommitOutcome | _SmokeOutcome)
        if isinstance(second, _SmokeOutcome):
            assert second.supervised_turns == 1
            assert second.turns_completed == 2

    assert len(factory.supervised_bridges) == 2, (
        "the supervisor factory must have been consulted on EACH attempt; "
        f"got supervised_bridges={list(factory.supervised_bridges)!r}"
    )
    attempt1_bridge = factory.supervised_bridges[0]
    attempt2_bridge = factory.supervised_bridges[1]
    assert attempt1_bridge is dead_bridge, (
        "attempt 1 must be supervised over the dead_bridge the caller "
        f"handed in; got {attempt1_bridge!r} vs dead_bridge={dead_bridge!r}"
    )
    assert attempt2_bridge is not dead_bridge, (
        "attempt 2 must NOT be supervised over the dead_bridge the caller "
        "handed in -- the fresh-bridge contract requires a distinct object"
    )
    assert attempt2_bridge is healthy_bridge, (
        "attempt 2 must be supervised over the healthy_bridge the factory built"
    )
    assert attempt1_bridge.identity_tag != attempt2_bridge.identity_tag, (
        "attempt 1 and attempt 2 supervised bridges must carry distinct identity tags; "
        f"got {attempt1_bridge.identity_tag!r} == {attempt2_bridge.identity_tag!r}"
    )

    assert len(captured_events) == 2, (
        f"expected two execute_agent_effect invocations, got {captured_events!r}"
    )
    attempt1_event = captured_events[0]
    attempt2_event = captured_events[1]
    assert attempt1_event == PipelineEvent.AGENT_FAILURE, (
        "attempt 1 must end in AGENT_FAILURE because the supervised bridge is terminal; "
        f"got {attempt1_event!r}"
    )
    assert attempt2_event == PipelineEvent.AGENT_SUCCESS, (
        "attempt 2 must end in AGENT_SUCCESS because the supervised bridge is healthy; "
        f"got {attempt2_event!r}"
    )

    assert len(bridge_factory.calls) == 1, (
        "the bridge_factory must be consulted exactly once across both attempts "
        "(attempt 1's bridge was handed in directly; only attempt 2 needed a "
        f"fresh bridge); got calls={list(bridge_factory.calls)!r}"
    )

    assert dead_bridge.shutdown_count >= 1, (
        "the dead bridge must be torn down after the terminal error, not reused"
    )
    assert healthy_bridge.shutdown_count >= 1, (
        "the healthy bridge must be shut down once it has finished its attempt"
    )

    _assert_no_traceback_or_crash(recorder)
    assert len(factory.supervisors) == 2
    assert factory.terminal.is_set()
    assert list(factory.interrupted_labels) == [
        f"{_AGENT_PROCESS_LABEL_PREFIX}{factory.label_scopes[0]}:"
    ], (
        "only attempt 1's supervisor must have interrupted its agent processes; "
        "attempt 2 was healthy and never interrupted"
    )
