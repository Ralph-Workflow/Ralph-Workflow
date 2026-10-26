"""Tests for RecoveryController wall-time clock, public seam, and workspace observation."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

import ralph.agents.invoke as invoke_module
from ralph.agents.invoke import AgentInvocationError, InvokeOptions, invoke_agent
from ralph.agents.system_clock import SystemClock
from ralph.agents.timeout_clock import FakeClock
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport
from ralph.pipeline.agent_chain_state import AgentChainState
from ralph.pipeline.state import PipelineState
from ralph.recovery.classifier import ClassifiedFailure, FailureCategory, FailureContext
from ralph.recovery.controller import RecoveryController, RecoveryControllerOptions
from ralph.recovery.events import FalloverEvent
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.probe import DefaultWorkspaceChangeProbe
from tests.recovery._scenario_harness import _build_policy_bundle

if TYPE_CHECKING:
    from ralph.pipeline.events import AgentFailureEvent


class _MemoryFileBackend:
    """In-memory FileBackend implementation for testing."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()

    def exists(self, path: Path) -> bool:
        return str(path) in self.files or str(path) in self.dirs

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        del parents, exist_ok
        self.dirs.add(str(path))

    def read_text(self, path: Path, *, encoding: str = "utf-8") -> str:
        del encoding
        return self.files[str(path)]

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        del encoding
        self.files[str(path)] = content

    def read_bytes(self, path: Path) -> bytes:
        return self.files[str(path)].encode("utf-8")

    def write_bytes(self, path: Path, content: bytes) -> None:
        self.files[str(path)] = content.decode("utf-8")

    def replace(self, source: Path, destination: Path) -> None:
        content = self.files.pop(str(source))
        self.files[str(destination)] = content

    def sync_directory(self, path: Path) -> None:
        del path

    def unlink(self, path: Path, *, missing_ok: bool = False) -> None:
        if str(path) in self.files:
            del self.files[str(path)]
        elif not missing_ok:
            raise FileNotFoundError(str(path))

    def glob(self, path: Path, pattern: str) -> list[Path]:
        del path, pattern
        return [Path(p) for p in self.files]


def _make_failure(
    category: FailureCategory,
    reason: str = "failed",
    agent: str = "agent_a",
    phase: str = "development",
    counts: bool = True,
    raw_message: str = "",
) -> ClassifiedFailure:
    return ClassifiedFailure(
        category,
        reason,
        agent,
        phase,
        counts,
        None,
        raw_message or reason,
    )


def test_system_clock_wall_time_and_epoch() -> None:
    clock = SystemClock()
    assert clock.monotonic() > 0.0
    now = clock.now_utc()
    assert now.tzinfo == UTC
    assert clock.epoch() > 0.0


def test_fake_clock_wall_time_settable_and_advancing() -> None:
    wall = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
    clock = FakeClock(start=100.0, wall_time=wall)

    assert clock.monotonic() == 100.0
    assert clock.now_utc() == wall
    assert clock.epoch() == wall.timestamp()

    clock.advance(30.0)
    assert clock.monotonic() == 130.0
    assert clock.now_utc() == datetime(2026, 10, 25, 12, 0, 30, tzinfo=UTC)
    assert clock.epoch() == wall.timestamp() + 30.0

    clock.sleep(15.0)
    assert clock.monotonic() == 145.0
    assert clock.now_utc() == datetime(2026, 10, 25, 12, 0, 45, tzinfo=UTC)

    new_wall = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
    clock.set_wall_time(new_wall)
    assert clock.now_utc() == new_wall
    assert clock.epoch() == new_wall.timestamp()

    clock.set_epoch(1_800_000_000.0)
    assert clock.epoch() == 1_800_000_000.0
    assert clock.now_utc() == datetime.fromtimestamp(1_800_000_000.0, tz=UTC)


def test_controller_failure_event_timestamp_uses_injected_clock() -> None:
    fixed_time = datetime(2026, 10, 25, 15, 30, 0, tzinfo=UTC)
    clock = FakeClock(start=50.0, wall_time=fixed_time)
    controller = RecoveryController(options=RecoveryControllerOptions(clock=clock))

    failure = _make_failure(
        FailureCategory.ENVIRONMENTAL,
        reason="Network timeout",
    )
    state = PipelineState(phase="development")
    context = FailureContext(
        phase="development",
        agent="agent_a",
        classified_failure=failure,
    )

    _, _, failure_evt = controller.handle(state, "Network timeout", context)
    assert failure_evt.timestamp == fixed_time


def test_controller_fallover_event_timestamp_uses_injected_clock() -> None:
    fixed_time = datetime(2026, 10, 25, 16, 0, 0, tzinfo=UTC)
    clock = FakeClock(start=100.0, wall_time=fixed_time)

    bundle = _build_policy_bundle(["agent_a", "agent_b"])

    captured_events: list[object] = []
    options = RecoveryControllerOptions(
        clock=clock,
        policy_bundle=bundle,
        budgets={"development": {"agent_a": 0, "agent_b": 5}},
    )
    controller = RecoveryController(options=options)
    controller.event_bus.subscribe(captured_events.append)

    for _ in range(3):
        controller.record_attempt_fact(
            "development", "agent_a", artifact_valid=False, workspace_changed=False
        )

    chain = AgentChainState(agents=["agent_a", "agent_b"], current_index=0)
    state = PipelineState(phase="development").with_phase_chain("development", chain)
    failure = _make_failure(
        FailureCategory.AGENT,
        reason="Agent error",
        counts=True,
    )
    context = FailureContext(
        phase="development",
        agent="agent_a",
        classified_failure=failure,
    )

    new_state, _, _ = controller.handle(state, "Agent error", context)
    chain_state = new_state.chain_for_phase("development")
    assert chain_state is not None
    assert chain_state.current_index == 1

    fallover_events = [e for e in captured_events if isinstance(e, FalloverEvent)]
    assert len(fallover_events) == 1
    assert fallover_events[0].timestamp == fixed_time

    assert len(new_state.fallover_history) >= 1
    assert new_state.fallover_history[-1].timestamp_iso == fixed_time.isoformat()


def test_controller_reset_hint_backend_injected() -> None:
    backend = _MemoryFileBackend()
    controller = RecoveryController(reset_hint_backend=backend)

    failure = _make_failure(
        FailureCategory.ENVIRONMENTAL,
        reason="stale session",
        raw_message="Session token expired",
    )
    controller._write_session_reset_hint("development", failure)

    assert any("retry_hint" in path or "development" in path for path in backend.files)


def test_record_attempt_fact_public_seam() -> None:
    clock = FakeClock(start=0.0)
    controller = RecoveryController(
        options=RecoveryControllerOptions(
            clock=clock,
            agent_max_consecutive_unproductive=3,
        )
    )

    # 1. Registered sole agent: demotes on 3 consecutive unproductive attempts
    controller.register_phase_chain("development", ["sole_agent"])

    res1 = controller.record_attempt_fact(
        "development",
        "sole_agent",
        artifact_valid=False,
        workspace_changed=False,
        free_retry_category=True,
    )
    assert res1 is None

    res2 = controller.record_attempt_fact(
        "development",
        "sole_agent",
        artifact_valid=False,
        workspace_changed=False,
        free_retry_category=True,
    )
    assert res2 is None

    res3 = controller.record_attempt_fact(
        "development",
        "sole_agent",
        artifact_valid=False,
        workspace_changed=False,
        free_retry_category=True,
    )
    assert res3 is not None
    assert "Sole agent 'sole_agent' demoted in phase 'development'" in res3
    assert "consecutive_unproductive" in res3

    # 2. Multi-agent chain: demoting agent does NOT produce sole agent error
    controller.register_phase_chain("review", ["agent_a", "agent_b"])
    controller.record_attempt_fact(
        "review", "agent_a", artifact_valid=False, workspace_changed=False
    )
    controller.record_attempt_fact(
        "review", "agent_a", artifact_valid=False, workspace_changed=False
    )
    res_multi = controller.record_attempt_fact(
        "review",
        "agent_a",
        artifact_valid=False,
        workspace_changed=False,
    )
    assert res_multi is None


def test_invoke_agent_probe_stamping_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failed attempt's workspace-change fact travels on the public sink.

    The fact is measured by the REAL probe against a REAL workspace
    mutation and delivered as an immutable, attempt-scoped
    ``AgentFailureEvent`` through ``InvokeOptions.failure_observation_sink``.

    No exception attribute and no process-global enum attribute carries the
    fact: the observation is the only transport, so one attempt can never
    inherit another attempt's fact.
    """
    probe = DefaultWorkspaceChangeProbe()
    workspace = MemoryWorkspace()
    workspace.write("seed.txt", "initial content")

    config = AgentConfig(
        cmd="test-agent",
        transport=AgentTransport.CLAUDE,
    )
    prompt_file = tmp_path / "PROMPT.md"
    prompt_file.write_text("prompt", encoding="utf-8")

    # (a) Agent mutates workspace then fails
    def fake_subprocess_mutating(cmd: list[str], ctx: object) -> list[str]:
        del cmd, ctx
        workspace.write("mutated.txt", "new file added")
        raise AgentInvocationError("test-agent", 1, stderr="fail after mutating")

    monkeypatch.setattr(invoke_module, "run_subprocess_and_read_lines", fake_subprocess_mutating)

    mutating_observations: list[AgentFailureEvent] = []
    opts_mutating = InvokeOptions(
        show_progress=False,
        workspace_probe=probe,
        workspace=workspace,
        phase="development",
        agent_name="agent_a",
        failure_observation_sink=mutating_observations.append,
    )

    with pytest.raises(AgentInvocationError):
        list(invoke_agent(config, str(prompt_file), options=opts_mutating))

    assert len(mutating_observations) == 1
    mutated = mutating_observations[0]
    assert mutated.workspace_changed is True
    assert mutated.phase == "development"
    assert mutated.agent == "agent_a"
    assert mutated.artifact_valid is False

    # (b) Agent fails without mutating workspace
    def fake_subprocess_non_mutating(cmd: list[str], ctx: object) -> list[str]:
        del cmd, ctx
        raise AgentInvocationError("test-agent", 1, stderr="fail without mutating")

    monkeypatch.setattr(
        invoke_module, "run_subprocess_and_read_lines", fake_subprocess_non_mutating
    )

    unchanged_observations: list[AgentFailureEvent] = []
    opts_non_mutating = InvokeOptions(
        show_progress=False,
        workspace_probe=probe,
        workspace=workspace,
        phase="development",
        agent_name="agent_a",
        failure_observation_sink=unchanged_observations.append,
    )

    with pytest.raises(AgentInvocationError):
        list(invoke_agent(config, str(prompt_file), options=opts_non_mutating))

    assert len(unchanged_observations) == 1
    unchanged = unchanged_observations[0]
    assert unchanged.workspace_changed is False
    assert unchanged.phase == "development"
    assert unchanged.agent == "agent_a"
    assert unchanged.artifact_valid is False
