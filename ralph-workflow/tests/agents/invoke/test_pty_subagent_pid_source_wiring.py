from __future__ import annotations

from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.process.child_liveness import (
    ChildLivenessRegistry,
    ChildLivenessSubagentPidSource,
)


def _make_registry() -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )


class _FakeStrategy:
    def __init__(self, registry: ChildLivenessRegistry) -> None:
        self._registry = registry
        self._active_label_prefix = lambda: "agent:scope:"


def _build_reader_with_strategy(strategy: object) -> PtyLineReader:
    reader = PtyLineReader.__new__(PtyLineReader)
    object.__setattr__(reader, "_strategy", strategy)
    return reader


def test_pty_build_subagent_pid_source_returns_registry_source() -> None:
    registry = _make_registry()
    strategy = _FakeStrategy(registry)
    reader = _build_reader_with_strategy(strategy)

    pid_source = reader._build_subagent_pid_source()
    assert isinstance(pid_source, ChildLivenessSubagentPidSource), (
        f"PTY _build_subagent_pid_source MUST return"
        f" ChildLivenessSubagentPidSource; got {type(pid_source).__name__}"
    )
    assert pid_source._registry is registry, (
        "PTY _build_subagent_pid_source MUST use the strategy's ChildLivenessRegistry"
    )
    assert pid_source._scope_prefix == "agent:scope:", (
        f"PTY _build_subagent_pid_source MUST use the active label"
        f" prefix; got {pid_source._scope_prefix!r}"
    )


def test_pty_build_subagent_pid_source_returns_none_without_registry() -> None:

    class _NoRegistryStrategy:
        _registry = None

    reader = _build_reader_with_strategy(_NoRegistryStrategy())
    assert reader._build_subagent_pid_source() is None, (
        "PTY _build_subagent_pid_source MUST return None when the"
        " strategy has no ChildLivenessRegistry"
    )
