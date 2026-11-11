from __future__ import annotations

import json

from ralph.agents.completion_signals import CompletionSignals
from ralph.agents.execution_state import (
    AgentExecutionState,
    OpenCodeExecutionStrategy,
)
from ralph.process.child_liveness import (
    ChildActivitySnapshot,
    ChildLivenessRegistry,
    MutableRecord,
)
from ralph.process.liveness import FakeLivenessProbe
from tests.fake_handle import _FakeHandle

_DESCENDANT_WAIT_POLL_SECONDS = 0.5


class TestStaleScopedChildEvidenceTimeout:
    def test_classify_quiet_stale_registry_with_raw_descendants_returns_active(self) -> None:
        stale_registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=30.0,
            stale_label_ttl=60.0,
            exit_reconcile=5.0,
            now=lambda: 1000.0,
        )
        stale_registry._records["child-x"] = MutableRecord(
            child_id="child-x",
            scope_prefix="agent:test:",
            pid=12345,
            started_at=900.0,
            last_progress_at=960.0,
            last_heartbeat_at=960.0,
            last_known_phase="running",
        )

        strategy = OpenCodeExecutionStrategy(label_scope="test", registry=stale_registry)

        class _OldStyleProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False

        probe = _OldStyleProbe()
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.ACTIVE, (
            f"Stale registry + raw descendants must return ACTIVE, not {state!r}. "
            "Raw descendants must not override stale scoped evidence."
        )

    def test_classify_quiet_stale_probe_snapshot_with_raw_descendants_returns_active(
        self,
    ) -> None:
        stale_snapshot = ChildActivitySnapshot(
            scope_prefix="agent:test:",
            has_process=True,
            has_fresh_label=False,
            has_fresh_progress=False,
            oldest_live_child_seconds=600.0,
            active_count=1,
            terminal_count=0,
        )
        stale_probe = FakeLivenessProbe(snapshot=stale_snapshot)

        strategy = OpenCodeExecutionStrategy(label_scope="test")

        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, stale_probe)

        assert state == AgentExecutionState.ACTIVE, (
            f"Stale probe snapshot + raw descendants must return ACTIVE, not {state!r}."
        )

    def test_classify_quiet_fresh_child_still_returns_waiting_on_child(self) -> None:
        fresh_snapshot = ChildActivitySnapshot(
            scope_prefix="agent:test:",
            has_process=True,
            has_fresh_label=True,
            has_fresh_progress=True,
            oldest_live_child_seconds=5.0,
            active_count=1,
            terminal_count=0,
        )
        fresh_probe = FakeLivenessProbe(snapshot=fresh_snapshot)

        strategy = OpenCodeExecutionStrategy(label_scope="test")
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, fresh_probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            f"Fresh child activity must still return WAITING_ON_CHILD, not {state!r}."
        )

    def test_classify_quiet_no_scoped_evidence_with_raw_descendants_returns_waiting(self) -> None:
        strategy = OpenCodeExecutionStrategy(label_scope="test")

        class _EmptyProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False

            def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
                return ChildActivitySnapshot(
                    scope_prefix=scope_prefix,
                    has_process=False,
                    has_fresh_label=False,
                    has_fresh_progress=False,
                    oldest_live_child_seconds=None,
                    active_count=0,
                    terminal_count=0,
                )

        probe = _EmptyProbe()
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            "Raw descendants without scoped Ralph evidence must return "
            f"WAITING_ON_CHILD, not {state!r}."
        )

    def test_evidence_precedence_stale_registry_with_raw_descendants_returns_resumable(
        self,
    ) -> None:
        stale_registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=30.0,
            stale_label_ttl=60.0,
            exit_reconcile=5.0,
            now=lambda: 1000.0,
        )
        stale_registry._records["child-x"] = MutableRecord(
            child_id="child-x",
            scope_prefix="agent:test:",
            pid=12345,
            started_at=900.0,
            last_progress_at=960.0,
            last_heartbeat_at=960.0,
            last_known_phase="running",
        )

        strategy = OpenCodeExecutionStrategy(label_scope="test", registry=stale_registry)

        class _OldStyleProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False

        probe = _OldStyleProbe()
        handle = _FakeHandle(has_descendants=True)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals, probe)

        assert state == AgentExecutionState.RESUMABLE_CONTINUE, (
            "Stale registry + raw descendants in exit path must return "
            f"RESUMABLE_CONTINUE, not {state!r}."
        )

    def test_evidence_precedence_stale_probe_snapshot_returns_resumable(self) -> None:
        stale_snapshot = ChildActivitySnapshot(
            scope_prefix="agent:test:",
            has_process=True,
            has_fresh_label=False,
            has_fresh_progress=False,
            oldest_live_child_seconds=600.0,
            active_count=1,
            terminal_count=0,
        )
        stale_probe = FakeLivenessProbe(snapshot=stale_snapshot)

        strategy = OpenCodeExecutionStrategy(label_scope="test")
        handle = _FakeHandle(has_descendants=True)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals, stale_probe)

        assert state == AgentExecutionState.RESUMABLE_CONTINUE, (
            f"Stale probe snapshot in exit path must return RESUMABLE_CONTINUE, not {state!r}."
        )

    def test_evidence_precedence_fresh_child_still_returns_waiting_on_child(self) -> None:
        fresh_snapshot = ChildActivitySnapshot(
            scope_prefix="agent:test:",
            has_process=True,
            has_fresh_label=True,
            has_fresh_progress=True,
            oldest_live_child_seconds=5.0,
            active_count=1,
            terminal_count=0,
        )
        fresh_probe = FakeLivenessProbe(snapshot=fresh_snapshot)

        strategy = OpenCodeExecutionStrategy(label_scope="test")
        handle = _FakeHandle(has_descendants=True)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals, fresh_probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            f"Fresh child in exit path must still return WAITING_ON_CHILD, not {state!r}."
        )

    def test_classify_quiet_stale_registry_after_pruning_with_raw_descendants_returns_active(
        self,
    ) -> None:
        t = [0.0]
        registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=30.0,
            stale_label_ttl=60.0,
            exit_reconcile=5.0,
            now=lambda: t[0],
        )

        strategy = OpenCodeExecutionStrategy(label_scope="test", registry=registry)

        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "child-x"}))
        strategy.observe_line(
            json.dumps({"type": "child_progress", "child_id": "child-x", "phase": "running"})
        )

        t[0] = 1000.0
        registry.snapshot("agent:test:")
        assert not registry.has_records("agent:test:")

        class _OldStyleProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False

        probe = _OldStyleProbe()
        handle = _FakeHandle(has_descendants=True)

        state = strategy.classify_quiet(handle, probe)

        assert state == AgentExecutionState.ACTIVE, (
            f"Stale scoped records pruned before classify_quiet must still yield ACTIVE, "
            f"not {state!r}. Raw descendants must not override previously-stale scoped evidence."
        )

    def test_classify_exit_stale_registry_after_pruning_with_raw_descendants_returns_resumable(
        self,
    ) -> None:
        t = [0.0]
        registry = ChildLivenessRegistry(
            progress_ttl=30.0,
            heartbeat_ttl=30.0,
            stale_label_ttl=60.0,
            exit_reconcile=5.0,
            now=lambda: t[0],
        )
        strategy = OpenCodeExecutionStrategy(label_scope="test", registry=registry)
        strategy.observe_line(json.dumps({"type": "child_started", "child_id": "child-x"}))
        strategy.observe_line(
            json.dumps({"type": "child_progress", "child_id": "child-x", "phase": "running"})
        )

        t[0] = 1000.0
        registry.snapshot("agent:test:")
        assert not registry.has_records("agent:test:")

        class _OldStyleProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False

        state = strategy.classify_exit(
            _FakeHandle(has_descendants=True),
            CompletionSignals(False, False, ()),
            _OldStyleProbe(),
        )

        assert state == AgentExecutionState.RESUMABLE_CONTINUE

    def test_evidence_precedence_no_scoped_evidence_with_raw_descendants_returns_waiting(
        self,
    ) -> None:
        strategy = OpenCodeExecutionStrategy(label_scope="test")

        class _EmptyProbe:
            def any_agent_active(self, label_prefix: str) -> bool:
                return False

            def child_snapshot(self, scope_prefix: str) -> ChildActivitySnapshot:
                return ChildActivitySnapshot(
                    scope_prefix=scope_prefix,
                    has_process=False,
                    has_fresh_label=False,
                    has_fresh_progress=False,
                    oldest_live_child_seconds=None,
                    active_count=0,
                    terminal_count=0,
                )

        probe = _EmptyProbe()
        handle = _FakeHandle(has_descendants=True)
        signals = CompletionSignals(
            explicit_complete=False,
            required_artifact_present=False,
            artifact_types=(),
        )

        state = strategy.classify_exit(handle, signals, probe)

        assert state == AgentExecutionState.WAITING_ON_CHILD, (
            "No scoped evidence + raw descendants in exit must return "
            f"WAITING_ON_CHILD, not {state!r}."
        )
