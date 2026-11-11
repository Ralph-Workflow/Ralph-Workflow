from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY, MultimodalModelIdentity
from ralph.mcp.server.factory import McpServerHandle
from ralph.mcp.session_plan import SessionMcpPlan
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import Event, PipelineEvent, WorkerCompletedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    PhaseParallelization,
)
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.agents.executor import WorkerResult
from tests.integration import (
    _parallel_multimodal_runtime_e2e_helper__capturedcontext as capturedcontext_helper,
)
from tests.integration import (
    _parallel_multimodal_runtime_e2e_helper__fakeagentexecutorwithartifacts as artifacts_helper,
)
from tests.integration import (
    _parallel_multimodal_runtime_e2e_helper__sessioncontract as sessioncontract_helper,
)

pytestmark = pytest.mark.subprocess_e2e


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        allowed_directories=[f"src/{uid}"],
    )


class _FakeMcpServerFactory:
    def __init__(self, handle: McpServerHandle) -> None:
        self._handle = handle

    def build(self, _session: object) -> McpServerHandle:
        return self._handle


def _make_mock_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    para = PhaseParallelization(max_parallel_workers=max_workers, post_fanout_verification=False)
    dev_phase = MagicMock(requires_commit=False, drain="development", role="execution")
    dev_phase.parallelization = para
    bundle.pipeline.phases = {"development": dev_phase}
    bundle.agents = AgentsPolicy(
        agent_drains={"development": AgentDrainConfig(chain="default", drain_class="development")},
        agent_chains={"default": AgentChainConfig(agents=["default"])},
    )
    return bundle


def _setup_patches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_executor: FakeAgentExecutor,
) -> None:
    monkeypatch.setattr(
        "ralph.agents.subprocess_executor.SubprocessAgentExecutor",
        lambda *args, **kwargs: fake_executor,
    )
    monkeypatch.setattr(
        "ralph.display.parallel_display.ParallelDisplay",
        _FakeDisplay,
    )
    monkeypatch.setattr(
        "ralph.pipeline.checkpoint.save",
        lambda _state, *_args, **_kwargs: None,
    )
    mcp_factory = _FakeMcpServerFactory(
        McpServerHandle(
            endpoint="http://127.0.0.1:9999/mcp",
            pid=99_999,
            shutdown=lambda: None,
        )
    )
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory",
        lambda *args, **kwargs: mcp_factory,
    )


class _FakeDisplay:
    def emit(self, unit_id: str | None, line: str) -> None:
        del unit_id, line

    def set_status(self, unit_id: str, status: object) -> None:
        del unit_id, status

    def __enter__(self) -> _FakeDisplay:
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _run_fan_out_sync(
    effect: FanOutEffect,
    tmp_path: Path,
    contract: sessioncontract_helper._SessionContract,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PipelineState, capturedcontext_helper._CapturedContext]:
    captured = capturedcontext_helper._CapturedContext()
    units = effect.work_units

    runs = {
        unit.unit_id: FakeRun(outputs=[f"done-{unit.unit_id}"], exit_code=0, duration_ms=10)
        for unit in units
    }
    fake_executor = artifacts_helper._FakeAgentExecutorWithArtifacts(runs, tmp_path)

    _setup_patches(
        monkeypatch,
        tmp_path,
        fake_executor,
    )

    state = PipelineState(
        phase="development",
        work_units=units,
        phase_chains={"development": AgentChainState(agents=["claude"])},
    )
    policy_bundle = _make_mock_policy_bundle(max_workers=effect.max_workers)
    workspace_scope = WorkspaceScope(tmp_path)

    monkeypatch.setattr(
        "ralph.interrupt.asyncio_bridge.install_signal_handlers",
        lambda *args: None,
    )

    def _fake_build_session_mcp_plan(**kwargs: object) -> SessionMcpPlan:
        del kwargs
        return SessionMcpPlan(
            capabilities=contract.capabilities,
            model_identity=contract.model_identity,
            capability_profile=MagicMock(),
        )

    monkeypatch.setattr(
        "ralph.pipeline.fan_out.build_session_mcp_plan",
        _fake_build_session_mcp_plan,
    )

    async def _fake_run_fan_out(**kwargs: object) -> list[Event]:
        ctx = kwargs.get("ctx")
        if ctx is not None and ctx.same_workspace is not None:
            captured.session_drain = ctx.same_workspace.session_drain
            captured.session_capabilities = ctx.same_workspace.session_capabilities
            captured.session_model_identity = ctx.same_workspace.session_model_identity
            captured.session_capability_profile = ctx.same_workspace.session_capability_profile

        completion_queue: asyncio.Queue[WorkerResult] = asyncio.Queue()

        for unit in units:
            same_workspace = ctx.same_workspace if ctx is not None else None
            if same_workspace is not None:
                ns_root = same_workspace.worker_namespace_root or (
                    same_workspace.repo_root / ".agent" / "workers"
                )
                worker_namespace = ns_root / unit.unit_id
                for subdir in ("artifacts", "tmp", "logs", "handoffs"):
                    (worker_namespace / subdir).mkdir(parents=True, exist_ok=True)

            await coordinator._run_worker(
                unit,
                fake_executor,
                _FakeDisplay(),
                completion_queue,
                ctx,
            )

        events: list[Event] = [PipelineEvent.FAN_OUT_STARTED]
        for _ in units:
            result = await completion_queue.get()
            events.append(WorkerCompletedEvent(unit_id=result.unit_id, exit_code=result.exit_code))
        events.append(PipelineEvent.ALL_WORKERS_COMPLETE)
        return events

    monkeypatch.setattr(
        "ralph.pipeline.parallel.coordinator.run_fan_out",
        _fake_run_fan_out,
    )

    final_state = runner_module.execute_fan_out_sync(
        effect=effect,
        state=state,
        display=_FakeDisplay(),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    return final_state, captured


_TWO_WORKERS_EXPECTED = 2
_THREE_WORKERS_EXPECTED = 3


def _assert_workers_succeeded_in_summary(tmp_path: Path, unit_ids: tuple[str, ...]) -> int:
    summary_path = tmp_path / ".agent" / "artifacts" / "parallel_development_summary.md"
    assert summary_path.exists(), "fan-out must write parallel_development_summary.md"
    summary = summary_path.read_text(encoding="utf-8")
    for unit_id in unit_ids:
        assert f"- **{unit_id}**: succeeded" in summary
    return len(unit_ids)


@pytest.mark.integration
def test_workers_complete_successfully_with_multimodal_session_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    units = (
        _make_work_unit("unit-a"),
        _make_work_unit("unit-b"),
    )
    effect = FanOutEffect(work_units=units, max_workers=2)

    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    contract = sessioncontract_helper._SessionContract(
        drain="development",
        capabilities=frozenset({"media.read", "workspace.edit"}),
        model_identity=identity,
    )
    final_state, captured = _run_fan_out_sync(
        effect,
        tmp_path,
        contract=contract,
        monkeypatch=monkeypatch,
    )

    completed_workers = _assert_workers_succeeded_in_summary(tmp_path, ("unit-a", "unit-b"))
    assert completed_workers == _TWO_WORKERS_EXPECTED
    assert final_state.worker_states == {}, "successful wave must clear worker tracking"
    assert captured.session_drain == "development"
    assert captured.session_capabilities == frozenset({"media.read", "workspace.edit"})
    assert captured.session_model_identity == identity


@pytest.mark.integration
def test_multiple_workers_each_get_unique_session_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    units = (
        _make_work_unit("unit-multi-a"),
        _make_work_unit("unit-multi-b"),
        _make_work_unit("unit-multi-c"),
    )
    effect = FanOutEffect(work_units=units, max_workers=3)

    identity = MultimodalModelIdentity(provider="claude", model_id="claude-sonnet-4")
    contract = sessioncontract_helper._SessionContract(
        drain="development",
        capabilities=frozenset({"media.read"}),
        model_identity=identity,
    )
    final_state, captured = _run_fan_out_sync(
        effect,
        tmp_path,
        contract=contract,
        monkeypatch=monkeypatch,
    )

    completed_workers = _assert_workers_succeeded_in_summary(
        tmp_path, ("unit-multi-a", "unit-multi-b", "unit-multi-c")
    )
    assert completed_workers == _THREE_WORKERS_EXPECTED
    assert final_state.worker_states == {}, "successful wave must clear worker tracking"
    assert captured.session_drain == "development"
    assert captured.session_capabilities == frozenset({"media.read"})
    assert captured.session_model_identity == identity


@pytest.mark.integration
def test_claude_worker_completes_with_inline_image_capability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = _make_work_unit("unit-claude")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    contract = sessioncontract_helper._SessionContract(
        drain="development",
        capabilities=frozenset({"media.read"}),
        model_identity=identity,
    )
    final_state, captured = _run_fan_out_sync(
        effect,
        tmp_path,
        contract=contract,
        monkeypatch=monkeypatch,
    )

    _assert_workers_succeeded_in_summary(tmp_path, ("unit-claude",))
    assert final_state.worker_states == {}, "successful wave must clear worker tracking"

    assert captured.session_model_identity == identity
    assert captured.session_model_identity is not None
    assert captured.session_model_identity.provider == "claude"


@pytest.mark.integration
def test_gemini_worker_completes_with_typed_block_capability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = _make_work_unit("unit-gemini")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    identity = MultimodalModelIdentity(provider="gemini", model_id="gemini-2.0-flash")
    contract = sessioncontract_helper._SessionContract(
        drain="development",
        capabilities=frozenset({"media.read"}),
        model_identity=identity,
    )
    final_state, captured = _run_fan_out_sync(
        effect,
        tmp_path,
        contract=contract,
        monkeypatch=monkeypatch,
    )

    _assert_workers_succeeded_in_summary(tmp_path, ("unit-gemini",))
    assert final_state.worker_states == {}, "successful wave must clear worker tracking"

    assert captured.session_model_identity == identity
    assert captured.session_model_identity is not None
    assert captured.session_model_identity.provider == "gemini"


@pytest.mark.integration
def test_unknown_provider_worker_completes_with_replay_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = _make_work_unit("unit-unknown")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    contract = sessioncontract_helper._SessionContract(
        drain="development",
        capabilities=frozenset({"media.read"}),
        model_identity=UNKNOWN_IDENTITY,
    )
    final_state, captured = _run_fan_out_sync(
        effect,
        tmp_path,
        contract=contract,
        monkeypatch=monkeypatch,
    )

    _assert_workers_succeeded_in_summary(tmp_path, ("unit-unknown",))
    assert final_state.worker_states == {}, "successful wave must clear worker tracking"

    assert captured.session_model_identity == UNKNOWN_IDENTITY
    assert captured.session_model_identity is not None
    assert captured.session_model_identity.provider == "unknown"


@pytest.mark.integration
def test_worker_handoff_contains_multimodal_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = _make_work_unit("unit-handoff")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    contract = sessioncontract_helper._SessionContract(
        drain="development",
        capabilities=frozenset({"media.read"}),
        model_identity=identity,
    )
    _run_fan_out_sync(
        effect,
        tmp_path,
        contract=contract,
        monkeypatch=monkeypatch,
    )

    worker_handoffs = tmp_path / ".agent" / "workers" / "unit-handoff" / "handoffs"
    handoff_path = worker_handoffs / "DEVELOPMENT_RESULT.md"
    assert handoff_path.is_file(), (
        f"Expected DEVELOPMENT_RESULT.md in worker handoffs, got: {list(worker_handoffs.iterdir())}"
    )
    content = handoff_path.read_text()
    assert "unit-handoff" in content


@pytest.mark.integration
def test_worker_artifacts_contain_plan_markdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    unit = _make_work_unit("unit-artifacts")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    identity = MultimodalModelIdentity(provider="gemini", model_id="gemini-2.0-flash")
    contract = sessioncontract_helper._SessionContract(
        drain="development",
        capabilities=frozenset({"media.read"}),
        model_identity=identity,
    )
    _run_fan_out_sync(
        effect,
        tmp_path,
        contract=contract,
        monkeypatch=monkeypatch,
    )

    worker_artifacts = tmp_path / ".agent" / "workers" / "unit-artifacts" / "artifacts"
    plan_path = worker_artifacts / "plan.md"
    assert plan_path.is_file(), (
        f"Expected plan.md in worker artifacts, got: {list(worker_artifacts.iterdir())}"
    )
    plan_document = plan_path.read_text(encoding="utf-8")
    assert "type: plan" in plan_document
    assert "## Steps" in plan_document
    assert not (tmp_path / ".agent" / "tmp" / "development_multimodal_handoff.json").exists()
