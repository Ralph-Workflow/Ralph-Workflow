from __future__ import annotations

from typing import TYPE_CHECKING

from ._base import (
    AgyExecutionStrategy,
    CursorExecutionStrategy,
    KimiExecutionStrategy,
    OpenCodeExecutionStrategy,
    PiExecutionStrategy,
)

if TYPE_CHECKING:
    from ralph.process.child_liveness import ChildLivenessRegistry
    from ralph.process.monitor import SubagentPidSource

    from ._base import BaseExecutionStrategy


def _make_opencode_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
) -> BaseExecutionStrategy:
    return OpenCodeExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def _make_agy_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
) -> BaseExecutionStrategy:
    return AgyExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def _make_cursor_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
) -> BaseExecutionStrategy:
    return CursorExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def _make_kimi_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
) -> BaseExecutionStrategy:
    return KimiExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


def _make_pi_strategy(
    *,
    label_scope: str | None = None,
    registry: ChildLivenessRegistry | None = None,
    subagent_pid_source: SubagentPidSource | None = None,
) -> BaseExecutionStrategy:
    return PiExecutionStrategy(
        label_scope=label_scope, registry=registry, subagent_pid_source=subagent_pid_source
    )


__all__ = [
    "_make_agy_strategy",
    "_make_cursor_strategy",
    "_make_kimi_strategy",
    "_make_opencode_strategy",
    "_make_pi_strategy",
]
