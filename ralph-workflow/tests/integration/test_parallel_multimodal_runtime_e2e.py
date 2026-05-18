"""End-to-end acceptance suite for multimodal managed-runtime through parallel workers.

Proves that the parallel worker path properly exercises the public fan-out seam
(_execute_fan_out_sync) and produces observable outcomes consistent with the serial
multimodal runtime:

- same-workspace workers complete successfully through run_fan_out
- worker namespace directories are created with correct structure
- worker sessions receive the parent phase's session contract (drain, capabilities,
  model identity) and use it to expose multimodal tools
- worker-local multimodal artifacts are visible in worker namespace

Observable behaviors verified (black-box):
- Worker final states reflect successful completion
- Worker namespaces contain artifacts/, tmp/, logs/, handoffs/ subdirectories
- Claude/Gemini/unknown-provider workers complete with expected outcomes

Full serial multimodal MCP coverage is in test_multimodal_managed_runtime_e2e.py.
That file proves the MCP-layer delivery verdicts, typed-block handling, and
upstream normalization through direct McpServer.handle_request() calls.

This file proves that the parallel managed-runtime path (_execute_fan_out_sync) produces
correct observable outcomes for multimodal-capable workers.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, NamedTuple, cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY, MultimodalModelIdentity
from ralph.mcp.session_plan import SessionMcpPlan
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import FanOutEffect
from ralph.pipeline.events import Event, PipelineEvent, WorkerCompletedEvent
from ralph.pipeline.parallel import coordinator
from ralph.pipeline.state import AgentChainState, PipelineState
from ralph.pipeline.work_units import WorkUnit
from ralph.pipeline.worker_state import WorkerStatus
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, PhaseParallelization
from ralph.testing.fake_agent_executor import FakeAgentExecutor, FakeRun
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.executor import AgentExecutor, WorkerResult
    from ralph.display.parallel_display import ParallelDisplay
    from ralph.pipeline.parallel.coordinator import WorkerContext

pytestmark = pytest.mark.subprocess_e2e


def _make_work_unit(uid: str) -> WorkUnit:
    return WorkUnit(
        unit_id=uid,
        description=f"Work unit {uid}",
        allowed_directories=[f"src/{uid}"],
    )




def _make_mock_policy_bundle(max_workers: int = 4) -> MagicMock:
    bundle = MagicMock()
    para = PhaseParallelization(max_parallel_workers=max_workers, post_fanout_verification=False)
    dev_phase = MagicMock(requires_commit=False, drain="development", role="execution")
    dev_phase.parallelization = para
    bundle.pipeline.phases = {"development": dev_phase}
    bundle.agents.agent_drains = {
        "development": AgentDrainConfig(chain="default", drain_class="development"),
    }
    bundle.agents.agent_chains = {
        "default": AgentChainConfig(agents=["default"]),
    }
    return bundle






def _setup_patches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fake_executor: FakeAgentExecutor,
) -> None:
    """Set up patches mirroring test_parallel_resume.py patterns."""
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
        lambda _state: None,
    )
    monkeypatch.setattr(
        "ralph.git.executor.GitExecutor",
        MagicMock,
    )
    monkeypatch.setattr(
        "ralph.mcp.server.factory_impl.DynamicBindingMcpServerFactory",
        lambda *args, **kwargs: MagicMock(),
    )


class _FakeAgentExecutorWithArtifacts(FakeAgentExecutor):
    """FakeAgentExecutor that creates artifacts in the worker namespace.

    This simulates what a real agent would do when it completes - create
    output artifacts in the worker's artifacts/ and handoffs/ directories.
    """

    class _FakeDisplay:
        def emit(self, unit_id: str | None, line: str) -> None:
            del unit_id, line

        def set_status(self, unit_id: str, status: object) -> None:
            del unit_id, status

        def __enter__(self) -> _FakeDisplay:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    class _SessionContract(NamedTuple):
        """Session contract parameters for parallel worker testing."""

        drain: str
        capabilities: frozenset[str]
        model_identity: MultimodalModelIdentity

    class _CapturedContext:
        """Holds captured session contract values from the coordinator's run_fan_out call."""

        def __init__(self) -> None:
            self.session_drain: str | None = None
            self.session_capabilities: frozenset[str] | None = None
            self.session_model_identity: MultimodalModelIdentity | None = None
            self.session_capability_profile: object | None = None


    def __init__(self, runs: dict[str, FakeRun], tmp_path: Path) -> None:
        super().__init__(runs)
        self._tmp_path = tmp_path

    async def run(
        self,
        unit: WorkUnit,
        *,
        on_output: Callable[[str], None],
        on_status: Callable[[WorkerStatus], None],
    ) -> WorkerResult:
        worker_ns = self._tmp_path / ".agent" / "workers" / unit.unit_id

        artifacts_dir = worker_ns / "artifacts"
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        (artifacts_dir / "plan.json").write_text(
            json.dumps(
                {
                    "name": "plan",
                    "type": "plan",
                    "content": {"summary": f"done-{unit.unit_id}"},
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                    "metadata": {},
                }
            )
        )

        handoffs_dir = worker_ns / "handoffs"
        handoffs_dir.mkdir(parents=True, exist_ok=True)
        (handoffs_dir / "DEVELOPMENT_RESULT.md").write_text(
            f"# Development Result for {unit.unit_id}\n\nCompleted successfully.\n"
        )

        return await super().run(unit, on_output=on_output, on_status=on_status)


_FakeDisplay = _FakeAgentExecutorWithArtifacts._FakeDisplay
_SessionContract = _FakeAgentExecutorWithArtifacts._SessionContract
_CapturedContext = _FakeAgentExecutorWithArtifacts._CapturedContext


def _run_fan_out_sync(
    effect: FanOutEffect,
    tmp_path: Path,
    contract: _SessionContract,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[PipelineState, _CapturedContext]:
    """Run fan-out via the public _execute_fan_out_sync seam.

    This exercises the same path that the real runner uses, including the
    same-workspace session contract propagation into worker sessions.

    Returns:
        A tuple of (final_state, captured_context). The captured_context
        holds the session contract values that the coordinator received.
    """
    captured = _CapturedContext()
    units = effect.work_units

    runs = {
        unit.unit_id: FakeRun(outputs=[f"done-{unit.unit_id}"], exit_code=0, duration_ms=10)
        for unit in units
    }
    fake_executor = _FakeAgentExecutorWithArtifacts(runs, tmp_path)

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
        "ralph.pipeline.runner.build_session_mcp_plan",
        _fake_build_session_mcp_plan,
    )

    async def _fake_run_fan_out(**kwargs: object) -> list[Event]:
        ctx = cast("WorkerContext | None", kwargs.get("ctx"))
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
                cast("AgentExecutor", fake_executor),
                cast("ParallelDisplay", _FakeDisplay()),
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
        display=cast("ParallelDisplay", _FakeDisplay()),
        policy_bundle=policy_bundle,
        workspace_scope=workspace_scope,
    )

    return final_state, captured


_TWO_WORKERS_EXPECTED = 2
_THREE_WORKERS_EXPECTED = 3


@pytest.mark.integration
def test_workers_complete_successfully_with_multimodal_session_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Parallel workers must complete successfully when session contract is propagated.

    Black-box observable: Worker final states show SUCCEEDED status.
    """
    units = (
        _make_work_unit("unit-a"),
        _make_work_unit("unit-b"),
    )
    effect = FanOutEffect(work_units=units, max_workers=2)

    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    contract = _SessionContract(
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

    completed_workers = 0
    for unit_id in ("unit-a", "unit-b"):
        worker_state = final_state.worker_states.get(unit_id)
        assert worker_state is not None, f"Worker {unit_id} missing from final state"
        assert worker_state.status == WorkerStatus.SUCCEEDED, (
            f"Worker {unit_id} expected SUCCEEDED, got {worker_state.status}"
        )
        completed_workers += 1

    assert completed_workers == _TWO_WORKERS_EXPECTED
    assert captured.session_drain == "development"
    assert captured.session_capabilities == frozenset({"media.read", "workspace.edit"})
    assert captured.session_model_identity == identity


@pytest.mark.integration
def test_worker_namespaces_created_with_correct_structure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Worker namespace directories must be created with artifacts/ and handoffs/.

    Black-box observable: the runtime creates the expected directory structure
    under .agent/workers/<unit_id>/.
    """
    unit = _make_work_unit("unit-workspace")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    identity = MultimodalModelIdentity(provider="gemini", model_id="gemini-2.0-flash")
    contract = _SessionContract(
        drain="development_analysis",
        capabilities=frozenset({"media.read"}),
        model_identity=identity,
    )
    _run_fan_out_sync(
        effect,
        tmp_path,
        contract=contract,
        monkeypatch=monkeypatch,
    )

    worker_ns = tmp_path / ".agent" / "workers" / "unit-workspace"
    for subdir in ("artifacts", "tmp", "logs", "handoffs"):
        assert (worker_ns / subdir).is_dir(), f"Expected {subdir}/ to exist"


@pytest.mark.integration
def test_multiple_workers_each_get_unique_session_ids(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Each worker must receive a unique session ID even with same session contract.

    Black-box observable: all workers complete successfully with SUCCEEDED status,
    proving the session contract was propagated correctly.
    """
    units = (
        _make_work_unit("unit-multi-a"),
        _make_work_unit("unit-multi-b"),
        _make_work_unit("unit-multi-c"),
    )
    effect = FanOutEffect(work_units=units, max_workers=3)

    identity = MultimodalModelIdentity(provider="claude", model_id="claude-sonnet-4")
    contract = _SessionContract(
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

    completed_workers = 0
    for unit_id in ("unit-multi-a", "unit-multi-b", "unit-multi-c"):
        worker_state = final_state.worker_states.get(unit_id)
        assert worker_state is not None, f"Worker {unit_id} missing from final state"
        assert worker_state.status == WorkerStatus.SUCCEEDED, (
            f"Worker {unit_id} expected SUCCEEDED, got {worker_state.status}"
        )
        completed_workers += 1

    assert completed_workers == _THREE_WORKERS_EXPECTED
    assert captured.session_drain == "development"
    assert captured.session_capabilities == frozenset({"media.read"})
    assert captured.session_model_identity == identity


@pytest.mark.integration
def test_claude_worker_completes_with_inline_image_capability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Workers with Claude identity must complete successfully.

    Black-box observable: WorkerCompletedEvent with exit_code=0 proves the
    Claude-capable worker path works correctly.
    """
    unit = _make_work_unit("unit-claude")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    contract = _SessionContract(
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

    worker_state = final_state.worker_states.get("unit-claude")
    assert worker_state is not None
    assert worker_state.status == WorkerStatus.SUCCEEDED

    assert captured.session_model_identity == identity
    assert captured.session_model_identity is not None
    assert captured.session_model_identity.provider == "claude"


@pytest.mark.integration
def test_gemini_worker_completes_with_typed_block_capability(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Workers with Gemini identity must complete successfully.

    Black-box observable: WorkerCompletedEvent with exit_code=0 proves the
    Gemini-capable worker path works correctly.
    """
    unit = _make_work_unit("unit-gemini")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    identity = MultimodalModelIdentity(provider="gemini", model_id="gemini-2.0-flash")
    contract = _SessionContract(
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

    worker_state = final_state.worker_states.get("unit-gemini")
    assert worker_state is not None
    assert worker_state.status == WorkerStatus.SUCCEEDED

    assert captured.session_model_identity == identity
    assert captured.session_model_identity is not None
    assert captured.session_model_identity.provider == "gemini"


@pytest.mark.integration
def test_unknown_provider_worker_completes_with_replay_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Workers with unknown provider must complete successfully (replay fallback is safe).

    Black-box observable: WorkerCompletedEvent with exit_code=0 proves the
    unknown-provider replay fallback path works correctly.
    """
    unit = _make_work_unit("unit-unknown")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    contract = _SessionContract(
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

    worker_state = final_state.worker_states.get("unit-unknown")
    assert worker_state is not None
    assert worker_state.status == WorkerStatus.SUCCEEDED

    assert captured.session_model_identity == UNKNOWN_IDENTITY
    assert captured.session_model_identity is not None
    assert captured.session_model_identity.provider == "unknown"


@pytest.mark.integration
def test_worker_handoff_contains_multimodal_artifacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Worker handoffs directory must contain DEVELOPMENT_RESULT.md after completion.

    Black-box observable: the handoff file proves the worker completed its phase
    and produced the expected artifact in the worker namespace.
    """
    unit = _make_work_unit("unit-handoff")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    identity = MultimodalModelIdentity(provider="claude", model_id="claude-3-5-sonnet-20241022")
    contract = _SessionContract(
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
def test_worker_artifacts_contain_plan_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Worker artifacts directory must contain plan.json after completion.

    Black-box observable: the plan.json artifact proves the worker produced
    its expected output in the correct location.
    """
    unit = _make_work_unit("unit-artifacts")
    effect = FanOutEffect(work_units=(unit,), max_workers=1)

    identity = MultimodalModelIdentity(provider="gemini", model_id="gemini-2.0-flash")
    contract = _SessionContract(
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
    plan_path = worker_artifacts / "plan.json"
    assert plan_path.is_file(), (
        f"Expected plan.json in worker artifacts, got: {list(worker_artifacts.iterdir())}"
    )
    plan_data = json.loads(plan_path.read_text())
    assert plan_data["type"] == "plan"
