from __future__ import annotations

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
