"""CapabilitySet — lightweight set of Ralph capabilities."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.protocol.capability_mapping import Capability as RalphCapability
from ralph.mcp.protocol.capability_mapping import SessionDrain

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class CapabilitySet:
    """Lightweight set of Ralph capabilities."""

    def __init__(self, values: Iterable[RalphCapability] | None = None) -> None:
        self._values = frozenset(values or ())

    def contains(self, capability: RalphCapability) -> bool:
        return capability in self._values

    def insert(self, capability: RalphCapability) -> None:
        self._values = frozenset((*self._values, capability))

    def __iter__(self) -> Iterator[RalphCapability]:
        return iter(self._values)

    def iter(self) -> Iterable[RalphCapability]:
        return iter(self._values)

    def to_vec(self) -> tuple[RalphCapability, ...]:
        return tuple(self._values)

    @classmethod
    def defaults_for_drain(cls, drain: SessionDrain) -> CapabilitySet:
        return cls(DEFAULT_CAPABILITIES.get(drain, ()))

    @classmethod
    def from_identifiers(cls, identifiers: Iterable[str] | None) -> CapabilitySet:
        if not identifiers:
            return cls()
        values: list[RalphCapability] = []
        for identifier in identifiers:
            try:
                values.append(RalphCapability(identifier))
            except ValueError:
                continue
        return cls(values)


DEFAULT_CAPABILITIES: dict[SessionDrain, tuple[RalphCapability, ...]] = {
    SessionDrain.PLANNING: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.WORKSPACE_WRITE_EPHEMERAL,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.ARTIFACT_PLAN_WRITE,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.WEB_SEARCH,
        RalphCapability.WEB_VISIT,
        RalphCapability.UPSTREAM_TOOL_USE,
    ),
    SessionDrain.DEVELOPMENT_ANALYSIS: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.RUN_REPORT_PROGRESS,
        RalphCapability.WEB_SEARCH,
        RalphCapability.WEB_VISIT,
        RalphCapability.UPSTREAM_TOOL_USE,
    ),
    SessionDrain.DEVELOPMENT_COMMIT: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.RUN_REPORT_PROGRESS,
    ),
    SessionDrain.ANALYSIS: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.RUN_REPORT_PROGRESS,
        RalphCapability.WEB_VISIT,
        RalphCapability.UPSTREAM_TOOL_USE,
    ),
    SessionDrain.REVIEW: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.WORKSPACE_WRITE_EPHEMERAL,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.WEB_SEARCH,
        RalphCapability.WEB_VISIT,
        RalphCapability.UPSTREAM_TOOL_USE,
    ),
    SessionDrain.REVIEW_ANALYSIS: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.RUN_REPORT_PROGRESS,
        RalphCapability.WEB_SEARCH,
        RalphCapability.WEB_VISIT,
        RalphCapability.UPSTREAM_TOOL_USE,
    ),
    SessionDrain.DEVELOPMENT: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.WORKSPACE_WRITE_EPHEMERAL,
        RalphCapability.WORKSPACE_WRITE_TRACKED,
        RalphCapability.WORKSPACE_EDIT,
        RalphCapability.WORKSPACE_DELETE,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.PROCESS_EXEC_UNBOUNDED,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.RUN_REPORT_PROGRESS,
        RalphCapability.ENV_READ,
        RalphCapability.WEB_SEARCH,
        RalphCapability.WEB_VISIT,
        RalphCapability.WEB_DOWNLOAD,
        RalphCapability.UPSTREAM_TOOL_USE,
    ),
    SessionDrain.FIX: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.WORKSPACE_WRITE_TRACKED,
        RalphCapability.WORKSPACE_EDIT,
        RalphCapability.WORKSPACE_DELETE,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.PROCESS_EXEC_UNBOUNDED,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.RUN_REPORT_PROGRESS,
        RalphCapability.ENV_READ,
        RalphCapability.WEB_SEARCH,
        RalphCapability.WEB_VISIT,
        RalphCapability.WEB_DOWNLOAD,
        RalphCapability.UPSTREAM_TOOL_USE,
    ),
    SessionDrain.REVIEW_COMMIT: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.RUN_REPORT_PROGRESS,
    ),
    SessionDrain.COMMIT: (
        RalphCapability.WORKSPACE_READ,
        RalphCapability.WORKSPACE_METADATA_READ,
        RalphCapability.GIT_STATUS_READ,
        RalphCapability.GIT_DIFF_READ,
        RalphCapability.ARTIFACT_SUBMIT,
        RalphCapability.ARTIFACT_PLAN_READ,
        RalphCapability.PROCESS_EXEC_BOUNDED,
        RalphCapability.RUN_REPORT_PROGRESS,
    ),
}

__all__ = ["DEFAULT_CAPABILITIES", "CapabilitySet"]
