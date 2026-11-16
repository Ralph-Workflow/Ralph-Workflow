from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from loguru import logger

import ralph.cli.commands.commit as commit_cli
from ralph.agents.invoke import AgentInactivityTimeoutError, AttemptEnded, InactivityTimeoutOpts
from ralph.agents.registry import AgentRegistry
from ralph.agents.timeout_clock import FakeClock
from ralph.agents.watchdog_fire_reason import WatchdogFireReason
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import DisplayContext, make_display_context
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.auto_integrate_record import (
    IntegrationRecord,
    read_record,
    record_path,
    write_record,
)
from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.resolver_health import choose_resolver
from ralph.pipeline.conflict_resolution.session import ResolutionSession, invoke_resolution_agent
from ralph.pipeline.effect_executor import demotion_after_finalization
from ralph.pipeline.integration_diagnostic import (
    handoff_integration_diagnostic,
    handoff_pending_merge_repair,
)
from ralph.pipeline.operation_evidence import InMemoryOperationEvidenceCollector, OperationFact
from ralph.pipeline.plumbing import commit_plumbing
from ralph.policy.models import AgentsPolicy
from ralph.project_policy import cli_integration, remediation
from ralph.project_policy.cli_integration import LoadResult
from ralph.project_policy.pipeline_graph import PHASE_REMEDIATION
from ralph.recovery.agent_failure_ledger import AgentFailureLedger, Cooldown
from ralph.recovery.attempt_facts import AttemptFacts
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.unavailability_reason import (
    DEFAULT_UNAVAILABILITY_BACKOFF_POLICY,
    UnavailabilityReason,
)
from ralph.workspace.scope import WorkspaceScope
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests.project_policy.policy_corpus import policy_invocation_bundle
from tests.test_auto_integrate_record_idempotent import _RecordBackend

if TYPE_CHECKING:
    from ralph.pipeline.factory import PipelineDeps

_PRIMARY = "primary-agent"
_NEXT = "next-agent"
_CHAIN = (_PRIMARY, _NEXT)


def _controller(clock: FakeClock) -> RecoveryController:
    return RecoveryController(options=RecoveryControllerOptions(clock=clock))


def _no_activity_error(agent: str) -> AgentInactivityTimeoutError:
    return AgentInactivityTimeoutError(
        agent,
        30.0,
        [],
        InactivityTimeoutOpts(reason=WatchdogFireReason.NO_OUTPUT_AT_START),
    )


def _resolver_invoke(
    *,
    controller: RecoveryController,
    session: ResolutionSession,
    config: UnifiedConfig,
    deps: PipelineDeps,
    workspace_scope: WorkspaceScope,
    display_context: DisplayContext,
) -> Callable[[str, Path], bool]:
    bundle = policy_invocation_bundle()

    def invoke(agent: str, prompt: Path) -> bool:
        return bool(
            invoke_resolution_agent(
                agent_name=agent,
                prompt_path=prompt,
                config=config,
                pipeline_deps=deps,
                workspace_scope=workspace_scope,
                policy_bundle=bundle,
                display=None,
                display_context=display_context,
                session=session,
                require_completion_evidence=True,
            )
        )

    return invoke


def _capture_cooldown_lines(run: Callable[[], object]) -> list[str]:
    lines: list[str] = []
    sink = logger.add(lines.append, level="WARNING", format="{message}")
    try:
        run()
    finally:
        logger.remove(sink)
    return [line.strip() for line in lines if "cooldown_decision" in line]


def _assert_cooldown_line(
    lines: list[str],
    *,
    phase: str,
    reason: UnavailabilityReason,
    duration_ms: int,
) -> None:
    matching = [
        line
        for line in lines
        if f"phase={phase}" in line
        and f"agent={_PRIMARY}" in line
        and f"reason={reason}" in line
        and f"duration_ms={duration_ms}" in line
        and f"expiry_ms={duration_ms}" in line
    ]
    assert len(matching) == 1


@pytest.mark.parametrize(
    ("failure", "reason", "expected_invoked", "expected_duration_ms"),
    (
        (
            AttemptEnded(facts=AttemptFacts(artifact_valid=False, workspace_changed=False)),
            UnavailabilityReason.DEAD_AGENT_NO_PROGRESS,
            [_PRIMARY, _PRIMARY, _PRIMARY, _NEXT],
            60_000,
        ),
        (
            AgentInactivityTimeoutError(
                _PRIMARY,
                30.0,
                [],
                InactivityTimeoutOpts(reason=WatchdogFireReason.NO_OUTPUT_AT_START),
            ),
            UnavailabilityReason.NO_OUTPUT_AT_START,
            [_PRIMARY, _NEXT],
            DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[
                UnavailabilityReason.NO_OUTPUT_AT_START
            ].base_backoff_ms,
        ),
    ),
)
def test_conflict_resolution_real_invocation_regression_wu_c4_cools_and_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: AttemptEnded | AgentInactivityTimeoutError,
    reason: UnavailabilityReason,
    expected_invoked: list[str],
    expected_duration_ms: int,
) -> None:
    clock = FakeClock()
    controller = _controller(clock)
    session = ResolutionSession(recovery_controller=controller)
    config = UnifiedConfig(agents={name: AgentConfig(cmd=name) for name in _CHAIN})
    display_context = make_display_context()
    deps = make_test_pipeline_deps(display_context, recovery_clock=clock)
    invoked: list[str] = []

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> object:
        agent = getattr(effect, "agent_name", None)
        assert isinstance(agent, str)
        invoked.append(agent)
        if isinstance(failure, BaseException):
            raise failure
        return failure

    monkeypatch.setattr(effect_executor_module, "execute_agent_effect", invoke_effect)

    def run_attempts() -> None:
        for _ in range(4):
            choice = choose_resolver(controller, _CHAIN)
            if choice.agent is None:
                break
            invoke_resolution_agent(
                agent_name=choice.agent,
                prompt_path=tmp_path / "conflict-prompt.md",
                config=config,
                pipeline_deps=deps,
                workspace_scope=WorkspaceScope(tmp_path),
                policy_bundle=policy_invocation_bundle(),
                display=None,
                display_context=display_context,
                session=session,
                require_completion_evidence=True,
            )

    lines = _capture_cooldown_lines(run_attempts)

    assert invoked == expected_invoked
    _assert_cooldown_line(
        lines, phase=PHASE_RESOLUTION, reason=reason, duration_ms=expected_duration_ms
    )


def test_project_policy_real_invocation_regression_wu_c4_cools_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    bundle = policy_invocation_bundle()
    remediation_chain = bundle.agents.agent_chains[PHASE_REMEDIATION].model_copy(
        update={"agents": list(_CHAIN)}
    )
    bundle = bundle.model_copy(
        update={
            "agents": bundle.agents.model_copy(
                update={
                    "agent_chains": {
                        **bundle.agents.agent_chains,
                        PHASE_REMEDIATION: remediation_chain,
                    }
                }
            )
        }
    )
    config = UnifiedConfig(agents={name: AgentConfig(cmd=name) for name in _CHAIN})
    load_result = LoadResult(
        config=config,
        workspace_scope=WorkspaceScope("/test/project"),
        initial_state=None,
        policy_bundle=bundle,
        run_id="wu-c4-real-paths",
    )
    display_context = make_display_context()
    deps = make_test_pipeline_deps(display_context, recovery_clock=clock)
    invoked: list[str] = []

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> AttemptEnded:
        agent = getattr(effect, "agent_name", None)
        assert isinstance(agent, str)
        invoked.append(agent)
        return AttemptEnded(facts=AttemptFacts(artifact_valid=False, workspace_changed=False))

    monkeypatch.setattr(cli_integration, "SystemClock", lambda: clock)
    monkeypatch.setattr(effect_executor_module, "execute_agent_effect", invoke_effect)
    invoke = cli_integration.make_production_invoke_agent(
        load_result,
        deps,
        WorkspaceScope("/test/project"),
        None,
        display_context,
    )

    lines = _capture_cooldown_lines(
        lambda: [invoke(phase=PHASE_REMEDIATION, prompt_path="prompt.md") for _ in range(4)]
    )

    assert invoked == [_PRIMARY, _PRIMARY, _PRIMARY, _NEXT]
    _assert_cooldown_line(
        lines,
        phase=PHASE_REMEDIATION,
        reason=UnavailabilityReason.DEAD_AGENT_NO_PROGRESS,
        duration_ms=60_000,
    )


def test_pending_merge_repair_no_activity_real_handoff_cools_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path("/workspace")
    backend = _RecordBackend()
    clock = FakeClock()
    controller = _controller(clock)
    session = ResolutionSession(recovery_controller=controller)
    config = UnifiedConfig(agents={name: AgentConfig(cmd=name) for name in _CHAIN})
    display_context = make_display_context()
    deps = make_test_pipeline_deps(display_context, recovery_clock=clock)
    invoked: list[str] = []
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="original",
        pre_target_sha="target",
        merge_commit_pending=True,
        merge_commit_head="head",
        merge_commit_parent="parent",
        merge_commit_tree="tree",
    )

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> object:
        agent = getattr(effect, "agent_name", None)
        assert isinstance(agent, str)
        invoked.append(agent)
        raise _no_activity_error(agent)

    monkeypatch.setattr(effect_executor_module, "execute_agent_effect", invoke_effect)
    invoke = _resolver_invoke(
        controller=controller,
        session=session,
        config=config,
        deps=deps,
        workspace_scope=WorkspaceScope(root),
        display_context=display_context,
    )

    def run_attempts() -> None:
        for _ in range(2):
            stored = (
                IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
                if backend.exists(record_path(root))
                else record
            )
            handoff_pending_merge_repair(
                root=root,
                record=stored,
                failure="pre-commit blocker",
                agents=_CHAIN,
                invoke=invoke,
                controller=controller,
                backend=backend,
            )

    lines = _capture_cooldown_lines(run_attempts)

    duration_ms = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[
        UnavailabilityReason.NO_OUTPUT_AT_START
    ].base_backoff_ms
    status = controller.agent_eligibility(PHASE_RESOLUTION, _PRIMARY)

    assert invoked == [_PRIMARY, _NEXT]
    assert not status.is_eligible
    assert status.reason == str(UnavailabilityReason.NO_OUTPUT_AT_START)
    _assert_cooldown_line(
        lines,
        phase=PHASE_RESOLUTION,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        duration_ms=duration_ms,
    )


def test_integration_diagnostic_no_activity_real_handoff_cools_and_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _RecordBackend()
    clock = FakeClock()
    controller = _controller(clock)
    session = ResolutionSession(recovery_controller=controller)
    config = UnifiedConfig(agents={name: AgentConfig(cmd=name) for name in _CHAIN})
    display_context = make_display_context()
    deps = make_test_pipeline_deps(display_context, recovery_clock=clock)
    invoked: list[str] = []
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        resolving_rebase=True,
        owning_worktree=str(tmp_path),
    )
    write_record(tmp_path, record, backend=backend)
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> object:
        agent = getattr(effect, "agent_name", None)
        assert isinstance(agent, str)
        invoked.append(agent)
        raise _no_activity_error(agent)

    monkeypatch.setattr(effect_executor_module, "execute_agent_effect", invoke_effect)
    invoke = _resolver_invoke(
        controller=controller,
        session=session,
        config=config,
        deps=deps,
        workspace_scope=WorkspaceScope(tmp_path),
        display_context=display_context,
    )

    def run_attempts() -> None:
        for _ in range(2):
            stored = read_record(tmp_path, backend=backend)
            assert stored is not None
            handoff_integration_diagnostic(
                root=tmp_path,
                record=stored,
                failure="completion proof unavailable",
                agents=_CHAIN,
                invoke=invoke,
                controller=controller,
                collector=collector,
                backend=backend,
                repair_attempt_cap=15,
            )

    lines = _capture_cooldown_lines(run_attempts)

    duration_ms = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[
        UnavailabilityReason.NO_OUTPUT_AT_START
    ].base_backoff_ms
    status = controller.agent_eligibility(PHASE_RESOLUTION, _PRIMARY)

    assert invoked == [_PRIMARY, _NEXT]
    assert not status.is_eligible
    assert status.reason == str(UnavailabilityReason.NO_OUTPUT_AT_START)
    _assert_cooldown_line(
        lines,
        phase=PHASE_RESOLUTION,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        duration_ms=duration_ms,
    )


def test_commit_message_real_invocation_regression_wu_c4_cools_and_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    config = UnifiedConfig(
        general=GeneralConfig(max_same_agent_retries=0),
        agents={name: AgentConfig(cmd=name) for name in _CHAIN},
    )
    registry = AgentRegistry.from_config(config)
    display_context = make_display_context()
    deps = make_test_pipeline_deps(
        display_context,
        registry_factory=lambda _config: registry,
        recovery_clock=clock,
    )
    invoked: list[str] = []

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> AttemptEnded:
        agent = getattr(effect, "agent_name", None)
        assert isinstance(agent, str)
        invoked.append(agent)
        return AttemptEnded(facts=AttemptFacts(artifact_valid=False, workspace_changed=False))

    monkeypatch.setattr(commit_plumbing, "execute_agent_effect", invoke_effect)
    monkeypatch.setattr(commit_cli, "delete_commit_message_artifacts", lambda _root: None)
    monkeypatch.setattr(
        commit_cli,
        "read_commit_message_artifact",
        lambda _root: "fix: selected fallback" if invoked[-1:] == [_NEXT] else None,
    )
    monkeypatch.setattr(
        commit_cli,
        "write_commit_prompt_file",
        lambda _root, _prompt: str(tmp_path / "commit-prompt.md"),
    )
    chain = commit_cli.CommitChainConfig(
        registry=registry,
        agents=list(_CHAIN),
        verbose=False,
        agents_policy=AgentsPolicy(),
        general_config=config,
    )

    lines = _capture_cooldown_lines(
        lambda: commit_plumbing.run_commit_plumbing(
            diff="diff --git a/file b/file",
            repo_root=tmp_path,
            chain_config=chain,
            display_context=display_context,
            pipeline_deps=deps,
        )
    )

    assert invoked == [_PRIMARY, _PRIMARY, _PRIMARY, _NEXT]
    _assert_cooldown_line(
        lines,
        phase="commit",
        reason=UnavailabilityReason.DEAD_AGENT_NO_PROGRESS,
        duration_ms=60_000,
    )


def test_pending_merge_repair_real_handoff_regression_wu_c4_cools_and_falls_back() -> None:
    root = Path("/workspace")
    backend = _RecordBackend()
    clock = FakeClock()
    controller = _controller(clock)
    invoked: list[str] = []
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="original",
        pre_target_sha="target",
        merge_commit_pending=True,
        merge_commit_head="head",
        merge_commit_parent="parent",
        merge_commit_tree="tree",
    )

    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return True

    def run_attempts() -> None:
        for _ in range(4):
            stored = (
                IntegrationRecord.model_validate_json(backend.read_bytes(record_path(root)))
                if backend.exists(record_path(root))
                else record
            )
            handoff_pending_merge_repair(
                root=root,
                record=stored,
                failure="pre-commit blocker",
                agents=_CHAIN,
                invoke=invoke,
                controller=controller,
                backend=backend,
            )

    lines = _capture_cooldown_lines(run_attempts)

    assert invoked == [_PRIMARY, _PRIMARY, _PRIMARY, _NEXT]
    _assert_cooldown_line(
        lines,
        phase=PHASE_RESOLUTION,
        reason=UnavailabilityReason.DEAD_AGENT_NO_PROGRESS,
        duration_ms=60_000,
    )


def test_integration_diagnostic_real_handoff_regression_wu_c4_cools_and_falls_back(
    tmp_path: Path,
) -> None:
    backend = _RecordBackend()
    clock = FakeClock()
    controller = _controller(clock)
    invoked: list[str] = []
    record = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="0" * 40,
        pre_target_sha="1" * 40,
        resolving_rebase=True,
        owning_worktree=str(tmp_path),
    )
    write_record(tmp_path, record, backend=backend)
    collector = InMemoryOperationEvidenceCollector(
        facts=(OperationFact(kind="rebase_object_resolvable", value="True"),)
    )

    def invoke(agent: str, _prompt: Path) -> bool:
        invoked.append(agent)
        return True

    def run_attempts() -> None:
        for _ in range(4):
            stored = read_record(tmp_path, backend=backend)
            assert stored is not None
            handoff_integration_diagnostic(
                root=tmp_path,
                record=stored,
                failure="completion proof unavailable",
                agents=_CHAIN,
                invoke=invoke,
                controller=controller,
                collector=collector,
                backend=backend,
                repair_attempt_cap=15,
            )

    lines = _capture_cooldown_lines(run_attempts)

    assert invoked == [_PRIMARY, _PRIMARY, _PRIMARY, _NEXT]
    _assert_cooldown_line(
        lines,
        phase=PHASE_RESOLUTION,
        reason=UnavailabilityReason.DEAD_AGENT_NO_PROGRESS,
        duration_ms=60_000,
    )


def test_commit_message_no_activity_real_invocation_cools_and_falls_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    config = UnifiedConfig(
        general=GeneralConfig(max_same_agent_retries=0),
        agents={name: AgentConfig(cmd=name) for name in _CHAIN},
    )
    registry = AgentRegistry.from_config(config)
    display_context = make_display_context()
    deps = make_test_pipeline_deps(
        display_context,
        registry_factory=lambda _config: registry,
        recovery_clock=clock,
    )
    invoked: list[str] = []

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> object:
        agent = getattr(effect, "agent_name", None)
        assert isinstance(agent, str)
        invoked.append(agent)
        raise _no_activity_error(agent)

    monkeypatch.setattr(commit_plumbing, "execute_agent_effect", invoke_effect)
    monkeypatch.setattr(commit_cli, "delete_commit_message_artifacts", lambda _root: None)
    monkeypatch.setattr(
        commit_cli,
        "read_commit_message_artifact",
        lambda _root: "fix: selected fallback" if invoked[-1:] == [_NEXT] else None,
    )
    monkeypatch.setattr(
        commit_cli,
        "write_commit_prompt_file",
        lambda _root, _prompt: str(tmp_path / "commit-prompt.md"),
    )
    chain = commit_cli.CommitChainConfig(
        registry=registry,
        agents=list(_CHAIN),
        verbose=False,
        agents_policy=AgentsPolicy(),
        general_config=config,
    )

    lines = _capture_cooldown_lines(
        lambda: commit_plumbing.run_commit_plumbing(
            diff="diff --git a/file b/file",
            repo_root=tmp_path,
            chain_config=chain,
            display_context=display_context,
            pipeline_deps=deps,
        )
    )
    duration_ms = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[
        UnavailabilityReason.NO_OUTPUT_AT_START
    ].base_backoff_ms

    assert invoked == [_PRIMARY, _NEXT]
    _assert_cooldown_line(
        lines,
        phase="commit",
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        duration_ms=duration_ms,
    )


def test_ledger_only_demotion_emits_cooldown_decision_with_duration_and_expiry() -> None:
    clock = FakeClock(start=0.0)
    ledger = AgentFailureLedger(clock=clock, consecutive_limit=1)
    duration_ms = 60_000

    lines = _capture_cooldown_lines(
        lambda: demotion_after_finalization(
            None,
            ledger,
            phase="development",
            agent=_PRIMARY,
            workspace_changed=False,
            exc=RuntimeError("unproductive attempt"),
        )
    )

    status = ledger.status("development", _PRIMARY)
    assert isinstance(status, Cooldown)
    _assert_cooldown_line(
        lines,
        phase="development",
        reason=UnavailabilityReason.DEAD_AGENT_NO_PROGRESS,
        duration_ms=duration_ms,
    )


def test_project_policy_no_activity_real_invocation_cools_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    bundle = policy_invocation_bundle()
    remediation_chain = bundle.agents.agent_chains[PHASE_REMEDIATION].model_copy(
        update={"agents": list(_CHAIN)}
    )
    bundle = bundle.model_copy(
        update={
            "agents": bundle.agents.model_copy(
                update={
                    "agent_chains": {
                        **bundle.agents.agent_chains,
                        PHASE_REMEDIATION: remediation_chain,
                    }
                }
            )
        }
    )
    config = UnifiedConfig(agents={name: AgentConfig(cmd=name) for name in _CHAIN})
    load_result = LoadResult(
        config=config,
        workspace_scope=WorkspaceScope("/test/project"),
        initial_state=None,
        policy_bundle=bundle,
        run_id="wu-u9-policy-no-activity",
    )
    display_context = make_display_context()
    deps = make_test_pipeline_deps(display_context, recovery_clock=clock)
    invoked: list[str] = []

    def invoke_effect(effect: object, *_args: object, **_kwargs: object) -> object:
        agent = getattr(effect, "agent_name", None)
        assert isinstance(agent, str)
        invoked.append(agent)
        raise _no_activity_error(agent)

    monkeypatch.setattr(cli_integration, "SystemClock", lambda: clock)
    monkeypatch.setattr(effect_executor_module, "execute_agent_effect", invoke_effect)
    invoke = cli_integration.make_production_invoke_agent(
        load_result,
        deps,
        WorkspaceScope("/test/project"),
        None,
        display_context,
    )

    def run_attempts() -> None:
        for _ in range(2):
            with pytest.raises(remediation.RemediationInvocationError):
                invoke(phase=PHASE_REMEDIATION, prompt_path="prompt.md")

    lines = _capture_cooldown_lines(run_attempts)
    duration_ms = DEFAULT_UNAVAILABILITY_BACKOFF_POLICY[
        UnavailabilityReason.NO_OUTPUT_AT_START
    ].base_backoff_ms

    assert invoked == [_PRIMARY, _NEXT]
    _assert_cooldown_line(
        lines,
        phase=PHASE_REMEDIATION,
        reason=UnavailabilityReason.NO_OUTPUT_AT_START,
        duration_ms=duration_ms,
    )
