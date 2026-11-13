from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.invoke.attempt_supervision._teardown_fn import _TeardownFn

if TYPE_CHECKING:
    from ralph.process.teardown import ProcessTeardown

TeardownFn = _TeardownFn


def resolve_teardown(process_teardown: ProcessTeardown | None, default: TeardownFn) -> TeardownFn:
    if process_teardown is not None:
        return process_teardown.teardown_subtree
    return default


__all__ = ["TeardownFn", "resolve_teardown"]
