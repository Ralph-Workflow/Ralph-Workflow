"""Tests for InvocationContext."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from ralph.agents.execution_state._base import BaseExecutionStrategy
from ralph.agents.invocation_context import InvocationContext
from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.agents.spec import AgentSpec
from ralph.agents.support import AgentSupport
from ralph.config.agent_config import AgentConfig
from ralph.config.enums import AgentTransport


class _FakeRegistry:
    def snapshot(self, prefix: str = "") -> object:
        return None

    def register_child(self, child_id: str, scope_prefix: str, *, pid: int | None = None) -> None:
        pass

    def record_progress(self, child_id: str, *, phase: str | None = None) -> None:
        pass

    def record_heartbeat(self, child_id: str) -> None:
        pass

    def record_terminal_ack(self, child_id: str, *, terminal_state: str = "complete") -> None:
        pass

    def has_records(self, scope_prefix: str = "") -> bool:
        return False


class _FakeClock:
    def monotonic(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        del seconds

    def wait_for_event(self, event: object, seconds: float) -> bool:
        del event, seconds
        return True


def _make_fake_parser() -> AgentOutputLine:
    return AgentOutputLine(type="text", content="test", raw="test")


class _FakeStrategy(BaseExecutionStrategy):
    pass


class TestInvocationContext:
    """Black-box tests for InvocationContext."""

    def test_frozen(self) -> None:
        ctx = InvocationContext(
            clock=_FakeClock(),
            liveness_registry=_FakeRegistry(),
        )
        with pytest.raises(FrozenInstanceError):
            del ctx.clock

    def test_default_fields(self) -> None:
        ctx = InvocationContext(
            clock=_FakeClock(),
            liveness_registry=_FakeRegistry(),
        )
        assert ctx.label_scope is None
        assert ctx.subagent_activity_sink is None
        assert ctx.agent_support is None

    def test_with_support_preserves_fields(self) -> None:
        registry = _FakeRegistry()
        clock = _FakeClock()
        ctx = InvocationContext(
            clock=clock,
            liveness_registry=registry,
            label_scope="test-scope",
        )
        support = AgentSupport(
            name="test",
            spec=AgentSpec(name="test", transport=AgentTransport.GENERIC),
            parser_factory=_make_fake_parser,
            strategy_factory=_FakeStrategy,
            config=AgentConfig(cmd="test"),
        )
        ctx2 = ctx.with_support(support)
        assert ctx2.agent_support is support
        assert ctx2.clock is clock
        assert ctx2.liveness_registry is registry
        assert ctx2.label_scope == "test-scope"
        assert ctx2.subagent_activity_sink is None
        assert ctx is not ctx2
