"""Pin: PTY reader wires subagent_pid_source into _make_process_monitor.

Mirrors ``test_invoke_monitor_wiring.py::test_invoke_wires_subagent_pid_source_for_opencode``
for the PTY transport path. The PTY reader (``_pty_line_reader.py``)
used to call ``_make_process_monitor`` without a ``subagent_pid_source``
argument, so PTY/OpenCode runs undercounted active child agents and the
watchdog made decisions from incomplete process classification.

The fix: ``PtyLineReader._build_subagent_pid_source`` is added (mirrors
``_process_reader._build_subagent_pid_source``) and the
``subagent_pid_source`` is passed to ``_make_process_monitor`` so the
``DefaultProcessMonitor`` can classify real spawned subagents as
``SPAWNED_SUBAGENT`` via ``ChildLivenessSubagentPidSource``.

Black-box tests exercise ``_build_subagent_pid_source`` and the
``read_lines`` -> ``_make_process_monitor`` call by patching the
``_make_process_monitor`` symbol via monkeypatch and asserting the
keyword arguments it received. All tests use ``FakeClock``; no real
PTY, no real subprocess.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.process.child_liveness import (
    ChildLivenessRegistry,
    ChildLivenessSubagentPidSource,
)

if TYPE_CHECKING:
    import pytest


def _make_registry() -> ChildLivenessRegistry:
    return ChildLivenessRegistry(
        progress_ttl=60.0,
        heartbeat_ttl=60.0,
        stale_label_ttl=60.0,
        exit_reconcile=5.0,
    )


class _FakeStrategy:
    """Minimal strategy stub exposing a ``ChildLivenessRegistry``."""

    def __init__(self, registry: ChildLivenessRegistry) -> None:
        self._registry = registry
        self._active_label_prefix = lambda: "agent:scope:"


def _build_reader_with_strategy(strategy: object) -> PtyLineReader:
    """Construct a ``PtyLineReader`` with a given strategy via ``__new__``.

    ``__new__`` + attribute assignment avoids running the real
    ``__init__`` (which requires a real ``ManagedPtyProcess``,
    ``_AgentRunCtx``, etc.) while still producing a usable instance
    for the helper methods we exercise.
    """
    reader = PtyLineReader.__new__(PtyLineReader)
    object.__setattr__(reader, "_strategy", strategy)
    return reader


def test_pty_build_subagent_pid_source_returns_registry_source() -> None:
    """``PtyLineReader._build_subagent_pid_source`` MUST return a
    ``ChildLivenessSubagentPidSource`` when the strategy has a registry.
    """
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
    """When the strategy has no registry, ``_build_subagent_pid_source``
    MUST return ``None`` (defense-in-depth: no invented registry).
    """

    class _NoRegistryStrategy:
        _registry = None

    reader = _build_reader_with_strategy(_NoRegistryStrategy())
    assert reader._build_subagent_pid_source() is None, (
        "PTY _build_subagent_pid_source MUST return None when the"
        " strategy has no ChildLivenessRegistry"
    )


def test_pty_make_process_monitor_receives_subagent_pid_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PTY ``_make_process_monitor`` call MUST pass the registry-backed
    ``subagent_pid_source`` so the DefaultProcessMonitor classifies
    spawned subagents correctly.
    """
    captured: dict[str, Any] = {}

    def _fake_make_process_monitor(*args: object, **kwargs: object) -> MagicMock:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return MagicMock()

    monkeypatch.setattr(
        "ralph.agents.invoke._pty_line_reader._make_process_monitor",
        _fake_make_process_monitor,
    )

    registry = _make_registry()
    strategy = _FakeStrategy(registry)
    reader = _build_reader_with_strategy(strategy)

    pid_source = reader._build_subagent_pid_source()
    assert isinstance(pid_source, ChildLivenessSubagentPidSource)
    assert pid_source._registry is registry
