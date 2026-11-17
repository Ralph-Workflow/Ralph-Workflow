from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, TypeGuard

from ralph._typed_attrs import read_attr
from ralph.agents.invoke._invoke_options import _INVOKE_OPTS_UNSET
from ralph.agents.invoke._workspace import WorkspaceMonitor

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.agents.invoke._types import InvokeOptions
    from ralph.agents.invoke._workspace_change_classifier import WorkspaceChangeClassifier
    from ralph.pipeline.agent_retry_intent import AgentRetryAction


def _is_workspace_monitor_factory(
    value: object,
) -> TypeGuard[Callable[[Path, WorkspaceChangeClassifier | None], object]]:
    return callable(value)


def _looks_like_workspace_monitor(value: object) -> TypeGuard[WorkspaceMonitor]:
    return callable(read_attr(value, "set_on_event", None))


def _as_workspace_monitor_factory(
    value: object,
) -> Callable[[Path, WorkspaceChangeClassifier | None], WorkspaceMonitor | None]:
    if not _is_workspace_monitor_factory(value):
        raise TypeError("workspace_monitor_factory must be callable or None")

    def _factory(
        path: Path, classifier: WorkspaceChangeClassifier | None
    ) -> WorkspaceMonitor | None:
        result: object = value(path, classifier)
        if result is None:
            return None
        if not _looks_like_workspace_monitor(result):
            return None
        return result

    return _factory


def fresh_session_options(
    opts: InvokeOptions,
    *,
    prior_session_id: str | None = None,
    workspace_monitor_factory: (
        Callable[[Path, WorkspaceChangeClassifier | None], WorkspaceMonitor | None] | None | object
    ) = _INVOKE_OPTS_UNSET,
) -> InvokeOptions:
    del prior_session_id
    if workspace_monitor_factory is _INVOKE_OPTS_UNSET:
        return replace(opts, session_id=None)
    factory: Callable[[Path, WorkspaceChangeClassifier | None], WorkspaceMonitor | None] | None
    if workspace_monitor_factory is None:
        factory = None
    else:
        factory = _as_workspace_monitor_factory(workspace_monitor_factory)
    return replace(opts, session_id=None, workspace_monitor_factory=factory)


def resolve_resume_session_id(
    *,
    has_prior_session: bool,
    prior_session_id: str | None,
    recovery_action: str,
) -> str | None:
    if recovery_action not in {"fresh", "resume", "new_session_with_id"}:
        raise ValueError(
            f"unknown recovery_action: {recovery_action!r}; "
            "expected one of 'fresh', 'resume', 'new_session_with_id'"
        )
    if has_prior_session and not (isinstance(prior_session_id, str) and prior_session_id):
        raise ValueError("has_prior_session=True requires a non-empty prior_session_id")

    if recovery_action == "fresh" or not has_prior_session:
        return None
    return prior_session_id if isinstance(prior_session_id, str) else None


def recovery_action_for_failure_reason(
    failure_reason: str,
    *,
    has_prior_session: bool,
    reset_tool_registry: bool = False,
) -> AgentRetryAction:
    if not has_prior_session:
        return "fresh"
    if failure_reason in {
        "AgentInactivityTimeoutError",
        "OpenCodeResumableExitError",
    }:
        return "resume"
    if reset_tool_registry:
        return "resume"
    return "fresh"


__all__ = [
    "fresh_session_options",
    "recovery_action_for_failure_reason",
    "resolve_resume_session_id",
]
