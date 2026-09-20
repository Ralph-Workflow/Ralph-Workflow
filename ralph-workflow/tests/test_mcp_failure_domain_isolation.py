from __future__ import annotations

import pytest

from ralph.pipeline.session_bridge import scoped_reset_tool_registry_callback


class _SharedBridge:
    def __init__(self) -> None:
        self.resets = 0

    def reset_tool_registry(self) -> None:
        self.resets += 1


def test_scoped_reset_records_only_the_failing_invocation() -> None:
    bridge = _SharedBridge()
    seen: list[str] = []
    reset_a = scoped_reset_tool_registry_callback(bridge, "invocation-a", seen.append)
    reset_b = scoped_reset_tool_registry_callback(bridge, "invocation-b", seen.append)

    assert reset_a is not None
    assert reset_b is not None
    reset_a()

    assert bridge.resets == 1
    assert seen == ["invocation-a"]


def test_unscoped_reset_is_rejected() -> None:
    with pytest.raises(ValueError, match="invocation scope"):
        scoped_reset_tool_registry_callback(_SharedBridge(), "unscoped")


def test_unscoped_reset_export_is_removed() -> None:
    with pytest.raises(ImportError):
        exec("from ralph.pipeline.session_bridge import reset_tool_registry_callback")


def test_reset_is_coalesced_while_another_scope_is_active() -> None:
    bridge = _SharedBridge()
    reset_b = scoped_reset_tool_registry_callback(bridge, "invocation-b")

    class _ReentrantBridge(_SharedBridge):
        def reset_tool_registry(self) -> None:
            self.resets += 1
            assert reset_b is not None
            assert reset_b() is None

    bridge_a = _ReentrantBridge()
    reset_a = scoped_reset_tool_registry_callback(bridge_a, "invocation-a")
    reset_b = scoped_reset_tool_registry_callback(bridge_a, "invocation-b")
    reset_c = scoped_reset_tool_registry_callback(bridge_a, "invocation-c")

    assert reset_a is not None
    assert reset_c is not None
    reset_a()
    reset_c()

    assert bridge_a.resets == 2


def test_failed_reset_clears_the_gate() -> None:
    class _FailOnceBridge(_SharedBridge):
        def reset_tool_registry(self) -> None:
            self.resets += 1
            if self.resets == 1:
                raise RuntimeError("reset failed")

    bridge = _FailOnceBridge()
    reset_a = scoped_reset_tool_registry_callback(bridge, "invocation-a")
    reset_b = scoped_reset_tool_registry_callback(bridge, "invocation-b")

    assert reset_a is not None
    assert reset_b is not None
    with pytest.raises(RuntimeError, match="reset failed"):
        reset_a()
    reset_b()

    assert bridge.resets == 2
