"""MCP capability mapping for Ralph sessions.

Ports the Rust capability-mapping layer used to translate session drain and
policy outcomes into MCP access-control decisions.
"""

from __future__ import annotations

from enum import Enum, StrEnum
from importlib import import_module
from typing import TYPE_CHECKING, cast

from ralph.mcp.protocol._access_decision import AccessDecision
from ralph.mcp.protocol._access_denied_code import AccessDeniedCode
from ralph.mcp.protocol._access_mode import AccessMode
from ralph.mcp.protocol._drain_class import DrainClass
from ralph.mcp.protocol._mcp_capability import McpCapability
from ralph.mcp.protocol._policy_mode import PolicyMode
from ralph.mcp.protocol._policy_outcome import PolicyOutcome
from ralph.mcp.protocol._policy_outcome_status import PolicyOutcomeStatus
from ralph.mcp.protocol._session_drain import SessionDrain

if TYPE_CHECKING:
    from ralph.policy.models import AgentsPolicy


class Capability(StrEnum):
    """Internal Ralph capability vocabulary."""

    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE_EPHEMERAL = "workspace.write_ephemeral"
    WORKSPACE_WRITE_TRACKED = "workspace.write_tracked"
    WORKSPACE_METADATA_READ = "workspace.metadata_read"
    WORKSPACE_EDIT = "workspace.edit"
    WORKSPACE_DELETE = "workspace.delete"
    PROCESS_EXEC_BOUNDED = "process.exec_bounded"
    PROCESS_EXEC_UNBOUNDED = "process.exec_unbounded"
    ARTIFACT_SUBMIT = "artifact.submit"
    ARTIFACT_PLAN_READ = "artifact.plan_read"
    ARTIFACT_PLAN_WRITE = "artifact.plan_write"
    RUN_REPORT_PROGRESS = "run.report_progress"
    GIT_STATUS_READ = "git.status_read"
    GIT_DIFF_READ = "git.diff_read"
    GIT_WRITE = "git.write"
    ENV_READ = "env.read"
    ENV_WRITE = "env.write"
    UPSTREAM_TOOL_USE = "upstream.tool_use"
    WEB_SEARCH = "web.search"
    WEB_VISIT = "web.visit"
    WEB_DOWNLOAD = "web.download"
    MEDIA_READ = "media.read"
    ARTIFACT_PLAN_SUBMIT = "artifact.plan_submit"


MCP_TO_RALPH_CAPABILITY_MAP: dict[McpCapability, Capability] = {
    McpCapability.FILE_READ: Capability.WORKSPACE_READ,
    McpCapability.GIT_READ: Capability.GIT_STATUS_READ,
    McpCapability.PROCESS_EXEC: Capability.PROCESS_EXEC_BOUNDED,
    McpCapability.ARTIFACT_SUBMIT: Capability.ARTIFACT_SUBMIT,
    McpCapability.ARTIFACT_PLAN_READ: Capability.ARTIFACT_PLAN_READ,
    McpCapability.ARTIFACT_PLAN_WRITE: Capability.ARTIFACT_PLAN_WRITE,
    McpCapability.WORKSPACE_READ: Capability.WORKSPACE_READ,
    McpCapability.WORKSPACE_WRITE_EPHEMERAL: Capability.WORKSPACE_WRITE_EPHEMERAL,
    McpCapability.WORKSPACE_WRITE_TRACKED: Capability.WORKSPACE_WRITE_TRACKED,
    McpCapability.GIT_STATUS_READ: Capability.GIT_STATUS_READ,
    McpCapability.GIT_WRITE: Capability.GIT_WRITE,
    McpCapability.ENV_READ: Capability.ENV_READ,
    McpCapability.ENV_WRITE: Capability.ENV_WRITE,
    McpCapability.PROCESS_EXEC_BOUNDED: Capability.PROCESS_EXEC_BOUNDED,
    McpCapability.PROCESS_EXEC_UNBOUNDED: Capability.PROCESS_EXEC_UNBOUNDED,
    McpCapability.RUN_REPORT_PROGRESS: Capability.RUN_REPORT_PROGRESS,
    McpCapability.UPSTREAM_TOOL_USE: Capability.UPSTREAM_TOOL_USE,
    McpCapability.WEB_SEARCH: Capability.WEB_SEARCH,
    McpCapability.WEB_VISIT: Capability.WEB_VISIT,
    McpCapability.WEB_DOWNLOAD: Capability.WEB_DOWNLOAD,
    McpCapability.MEDIA_READ: Capability.MEDIA_READ,
    McpCapability.WORKSPACE_METADATA_READ: Capability.WORKSPACE_METADATA_READ,
    McpCapability.WORKSPACE_EDIT: Capability.WORKSPACE_EDIT,
    McpCapability.WORKSPACE_DELETE: Capability.WORKSPACE_DELETE,
    # Legacy alias kept for backward-compatibility; canonical capability is plan_write.
    McpCapability.ARTIFACT_PLAN_SUBMIT: Capability.ARTIFACT_PLAN_WRITE,
}

_RALPH_CAPABILITY_ALIASES: dict[str, Capability] = {
    "process_exec_bounded": Capability.PROCESS_EXEC_BOUNDED,
    "process_exec_unbounded": Capability.PROCESS_EXEC_UNBOUNDED,
    "process.exec_bounded": Capability.PROCESS_EXEC_BOUNDED,
    "process.exec_unbounded": Capability.PROCESS_EXEC_UNBOUNDED,
    "git.status.read": Capability.GIT_STATUS_READ,
    "git.status_read": Capability.GIT_STATUS_READ,
    "git.diff.read": Capability.GIT_DIFF_READ,
    "git.diff_read": Capability.GIT_DIFF_READ,
    "web.search": Capability.WEB_SEARCH,
    "web_search": Capability.WEB_SEARCH,
    "web.visit": Capability.WEB_VISIT,
    "web_visit": Capability.WEB_VISIT,
    "web.download": Capability.WEB_DOWNLOAD,
    "web_download": Capability.WEB_DOWNLOAD,
    "media.read": Capability.MEDIA_READ,
    "media_read": Capability.MEDIA_READ,
    "workspace.metadata_read": Capability.WORKSPACE_METADATA_READ,
    "workspace.metadata.read": Capability.WORKSPACE_METADATA_READ,
    "artifact.plan_read": Capability.ARTIFACT_PLAN_READ,
    "artifact.plan_write": Capability.ARTIFACT_PLAN_WRITE,
    "workspace.edit": Capability.WORKSPACE_EDIT,
    "workspace.delete": Capability.WORKSPACE_DELETE,
    "artifact.plan_submit": Capability.ARTIFACT_PLAN_WRITE,
}

_MCP_CAPABILITY_ALIASES: dict[str, McpCapability] = {
    "workspace.read": McpCapability.WORKSPACE_READ,
    "workspace.write_ephemeral": McpCapability.WORKSPACE_WRITE_EPHEMERAL,
    "workspace.write_tracked": McpCapability.WORKSPACE_WRITE_TRACKED,
    "artifact.submit": McpCapability.ARTIFACT_SUBMIT,
    "artifact.plan_read": McpCapability.ARTIFACT_PLAN_READ,
    "artifact.plan_write": McpCapability.ARTIFACT_PLAN_WRITE,
    "workspace.coordination": McpCapability.WORKSPACE_COORDINATION,
    "git.read": McpCapability.GIT_READ,
    "git.status.read": McpCapability.GIT_STATUS_READ,
    "git.status_read": McpCapability.GIT_STATUS_READ,
    "git.write": McpCapability.GIT_WRITE,
    "env.read": McpCapability.ENV_READ,
    "env.write": McpCapability.ENV_WRITE,
    "process.exec": McpCapability.PROCESS_EXEC,
    "process.exec_bounded": McpCapability.PROCESS_EXEC_BOUNDED,
    "process.exec_unbounded": McpCapability.PROCESS_EXEC_UNBOUNDED,
    "process_exec_bounded": McpCapability.PROCESS_EXEC_BOUNDED,
    "process_exec_unbounded": McpCapability.PROCESS_EXEC_UNBOUNDED,
    "run.report_progress": McpCapability.RUN_REPORT_PROGRESS,
    "file.read": McpCapability.FILE_READ,
    "file.write": McpCapability.FILE_WRITE,
    "upstream.tool_use": McpCapability.UPSTREAM_TOOL_USE,
    "upstream_tool_use": McpCapability.UPSTREAM_TOOL_USE,
    "web.search": McpCapability.WEB_SEARCH,
    "web_search": McpCapability.WEB_SEARCH,
    "web.visit": McpCapability.WEB_VISIT,
    "web_visit": McpCapability.WEB_VISIT,
    "web.download": McpCapability.WEB_DOWNLOAD,
    "web_download": McpCapability.WEB_DOWNLOAD,
    "media.read": McpCapability.MEDIA_READ,
    "media_read": McpCapability.MEDIA_READ,
    "workspace.metadata_read": McpCapability.WORKSPACE_METADATA_READ,
    "workspace.edit": McpCapability.WORKSPACE_EDIT,
    "workspace.delete": McpCapability.WORKSPACE_DELETE,
    "artifact.plan_submit": McpCapability.ARTIFACT_PLAN_WRITE,
}


def _policy_validation_error_type() -> type[Exception]:
    return cast(
        "type[Exception]",
        import_module("ralph.policy.validation").PolicyValidationError,
    )


_APPROVED_POLICY_VALUES = {"approved", "allow", "allowed"}
_DENIED_POLICY_VALUES = {"denied", "deny", "denied_by_policy"}
_APPROVED_WITH_RESTRICTION_VALUES = {
    "approvedwithrestriction",
    "approved_with_restriction",
    "allow_with_restriction",
    "allowed_with_restriction",
}


def normalize_token(value: str) -> str:
    """Normalize a capability or policy token to lowercase with underscores."""
    return value.strip().replace("-", "_").replace(" ", "_").lower()


def extract_text_field(value: object, field_name: str) -> str | None:
    """Extract a named string field from a dict or object attribute, returning None if absent."""
    if isinstance(value, dict):
        field_value = value.get(field_name)
    else:
        field_value = getattr(value, field_name, None)
    return field_value if isinstance(field_value, str) else None


def extract_named_value(value: object) -> str | None:
    """Extract the canonical string value from a string, Enum, or structured object."""
    if isinstance(value, str):
        return value
    if isinstance(value, Enum):
        enum_value = cast("object", value.value)
        if isinstance(enum_value, str):
            return enum_value
    for field_name in ("status", "name", "value"):
        field_value = extract_text_field(value, field_name)
        if field_value is not None:
            return field_value
    return None


def coerce_session_drain(value: SessionDrain | str) -> SessionDrain:
    """Coerce a string or SessionDrain to a SessionDrain, raising ValueError for unknown values."""
    if isinstance(value, SessionDrain):
        return value

    normalized = normalize_token(value)
    aliases = {
        "planning": SessionDrain.PLANNING,
        "development": SessionDrain.DEVELOPMENT,
        "development_analysis": SessionDrain.DEVELOPMENT_ANALYSIS,
        "development_commit": SessionDrain.DEVELOPMENT_COMMIT,
        "analysis": SessionDrain.ANALYSIS,
        "review": SessionDrain.REVIEW,
        "review_analysis": SessionDrain.REVIEW_ANALYSIS,
        "review_commit": SessionDrain.REVIEW_COMMIT,
        "fix": SessionDrain.FIX,
        "commit": SessionDrain.COMMIT,
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown session drain: {value!r}") from exc


def coerce_capability(value: Capability | str) -> Capability:
    """Coerce a string or Capability to a Capability enum, raising ValueError for unknown values."""
    if isinstance(value, Capability):
        return value

    normalized = normalize_token(value)
    for capability in Capability:
        if normalize_token(capability.value) == normalized:
            return capability

    try:
        return _RALPH_CAPABILITY_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown Ralph capability: {value!r}") from exc


def coerce_mcp_capability(value: McpCapability | str) -> McpCapability:
    """Coerce a string or McpCapability to a McpCapability enum."""
    if isinstance(value, McpCapability):
        return value

    normalized = normalize_token(value)
    for capability in McpCapability:
        if normalize_token(capability.value) == normalized:
            return capability

    try:
        return _MCP_CAPABILITY_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown McpCapability: {value!r}") from exc


def normalize_policy_outcome(value: object) -> PolicyOutcome:
    """Normalize any policy outcome representation to a PolicyOutcome."""
    if isinstance(value, PolicyOutcome):
        return value
    if value is True:
        return PolicyOutcome(status=PolicyOutcomeStatus.APPROVED)

    status_value = extract_named_value(value)
    normalized_status = normalize_token(status_value) if status_value is not None else ""
    reason = extract_text_field(value, "reason")
    restriction = extract_text_field(value, "restriction")

    status = resolved_policy_status(value, normalized_status, reason)
    if status is not None:
        return PolicyOutcome(status=status, reason=reason, restriction=restriction)

    raise ValueError(f"Unsupported policy outcome: {value!r}")


def resolved_policy_status(
    value: object,
    normalized_status: str,
    reason: str | None,
) -> PolicyOutcomeStatus | None:
    """Resolve a normalized status string to a PolicyOutcomeStatus, or None if unrecognized."""
    if normalized_status in _APPROVED_POLICY_VALUES:
        return PolicyOutcomeStatus.APPROVED
    if normalized_status in _APPROVED_WITH_RESTRICTION_VALUES:
        return PolicyOutcomeStatus.APPROVED_WITH_RESTRICTION
    if normalized_status in _DENIED_POLICY_VALUES:
        return PolicyOutcomeStatus.DENIED
    if isinstance(value, dict) and "reason" in value:
        return PolicyOutcomeStatus.DENIED
    if reason is not None:
        return PolicyOutcomeStatus.DENIED
    return None


def drain_class_for_drain_name(
    name: str,
    agents_policy: AgentsPolicy | None = None,
) -> DrainClass:
    """Resolve the drain class for any policy-declared drain name.

    Resolution order:
    1. Explicit drain_class on the AgentDrainConfig (highest priority).
    2. Drain name itself is a valid DrainClass value (fallback).
    3. PolicyValidationError when neither applies.
    """
    policy_validation_error = _policy_validation_error_type()
    if agents_policy is not None:
        drain_cfg = agents_policy.agent_drains.get(name)
        if drain_cfg is not None and drain_cfg.drain_class is not None:
            try:
                return DrainClass(drain_cfg.drain_class)
            except ValueError as err:
                raise policy_validation_error(
                    f"Drain '{name}' has invalid drain_class '{drain_cfg.drain_class}'; "
                    f"expected one of: planning, development, analysis, review, fix, commit."
                ) from err
    try:
        return DrainClass(name)
    except ValueError:
        pass
    raise policy_validation_error(
        f"Drain '{name}' has no drain_class declared in agents.toml; "
        f"add drain_class = '<class>' under [agent_drains.{name}] "
        f"(one of: planning, development, analysis, review, fix, commit)."
    )


def drain_class_for_session(
    drain: SessionDrain | str,
    agents_policy: AgentsPolicy | None = None,
) -> DrainClass:
    """Classify a session drain into its drain class.

    Resolution is policy-defined only: callers must supply ``agents_policy`` and
    the drain must be declared there with an explicit ``drain_class``.
    """
    if agents_policy is None:
        raise _policy_validation_error_type()(
            f"Drain {drain!r} cannot resolve drain_class without agents_policy; "
            "pass the active agents policy so drain_class is read from declarations"
        )

    return drain_class_for_drain_name(str(drain), agents_policy)


def drain_to_access_mode(
    drain: SessionDrain | str,
    agents_policy: AgentsPolicy | None = None,
) -> AccessMode:
    """Determine the MCP access mode for a session drain."""
    if drain_class_for_session(drain, agents_policy).allows_write():
        return AccessMode.READ_WRITE
    return AccessMode.READ_ONLY


def drain_to_policy_mode(
    drain: SessionDrain | str,
    agents_policy: AgentsPolicy | None = None,
) -> PolicyMode:
    """Map a session drain to the matching policy mode.

    Accepts any policy-declared drain name by resolving its class through
    drain_class_for_session. DrainClass and PolicyMode share the same
    vocabulary, so the mapping is a direct value lookup.
    """
    dc = drain_class_for_session(drain, agents_policy)
    return PolicyMode(dc.value)


def lookup_ralph_capability(capability: McpCapability | str) -> Capability | None:
    """Look up the Ralph capability mapped from an MCP capability."""
    try:
        normalized_capability = coerce_mcp_capability(capability)
    except ValueError:
        return None
    return MCP_TO_RALPH_CAPABILITY_MAP.get(normalized_capability)


def policy_from_outcome(outcome: object) -> AccessDecision:
    """Convert a Ralph policy outcome to an MCP access decision."""
    normalized_outcome = normalize_policy_outcome(outcome)
    if normalized_outcome.status in {
        PolicyOutcomeStatus.APPROVED,
        PolicyOutcomeStatus.APPROVED_WITH_RESTRICTION,
    }:
        return AccessDecision.allow()

    reason = normalized_outcome.reason or "denied"
    return AccessDecision.deny(reason, AccessDeniedCode.CAPABILITY_DENIED)


def evaluate_workspace_write(ephemeral: object, tracked: object) -> AccessDecision:
    """Evaluate the composite workspace-write policy."""
    ephemeral_outcome = normalize_policy_outcome(ephemeral)
    tracked_outcome = normalize_policy_outcome(tracked)
    allowed_statuses = {
        PolicyOutcomeStatus.APPROVED,
        PolicyOutcomeStatus.APPROVED_WITH_RESTRICTION,
    }
    if ephemeral_outcome.status in allowed_statuses or tracked_outcome.status in allowed_statuses:
        return AccessDecision.allow()
    return AccessDecision.deny(
        "Workspace write capability not granted",
        AccessDeniedCode.CAPABILITY_DENIED,
    )


def evaluate_mapped_capability(
    capability: McpCapability | str,
    mapped_outcome: tuple[Capability | str, object] | None,
) -> AccessDecision:
    """Evaluate access for a capability that maps directly to a Ralph capability."""
    try:
        normalized_capability = coerce_mcp_capability(capability)
    except ValueError:
        return AccessDecision.deny(
            f"Unknown capability: {capability!r}",
            AccessDeniedCode.CAPABILITY_DENIED,
        )

    if mapped_outcome is None:
        return AccessDecision.deny(
            f"Unknown capability: {normalized_capability.value}",
            AccessDeniedCode.CAPABILITY_DENIED,
        )

    mapped_capability, outcome = mapped_outcome
    coerce_capability(mapped_capability)
    return policy_from_outcome(outcome)


def check_mcp_capability_policy(
    capability: McpCapability | str,
    ephemeral: object,
    tracked: object,
    mapped_outcome: tuple[Capability | str, object] | None,
) -> AccessDecision:
    """Decide access for an MCP capability from session policy outcomes."""
    try:
        normalized_capability = coerce_mcp_capability(capability)
    except ValueError:
        return AccessDecision.deny(
            "Unrecognized McpCapability "
            f"{capability!r}: ralph-workflow has not been updated "
            "to handle this capability variant",
            AccessDeniedCode.CAPABILITY_DENIED,
        )

    if normalized_capability in {McpCapability.WORKSPACE_WRITE_ANY, McpCapability.FILE_WRITE}:
        return evaluate_workspace_write(ephemeral, tracked)
    if normalized_capability is McpCapability.WORKSPACE_COORDINATION:
        return AccessDecision.allow()
    if normalized_capability in {
        McpCapability.FILE_READ,
        McpCapability.GIT_READ,
        McpCapability.PROCESS_EXEC,
        McpCapability.ARTIFACT_SUBMIT,
        McpCapability.WORKSPACE_READ,
        McpCapability.WORKSPACE_WRITE_EPHEMERAL,
        McpCapability.WORKSPACE_WRITE_TRACKED,
        McpCapability.GIT_STATUS_READ,
        McpCapability.GIT_WRITE,
        McpCapability.ENV_READ,
        McpCapability.ENV_WRITE,
        McpCapability.PROCESS_EXEC_BOUNDED,
        McpCapability.PROCESS_EXEC_UNBOUNDED,
        McpCapability.RUN_REPORT_PROGRESS,
        McpCapability.UPSTREAM_TOOL_USE,
        McpCapability.WEB_SEARCH,
        McpCapability.WEB_VISIT,
        McpCapability.WEB_DOWNLOAD,
        McpCapability.MEDIA_READ,
        McpCapability.WORKSPACE_METADATA_READ,
        McpCapability.WORKSPACE_EDIT,
        McpCapability.WORKSPACE_DELETE,
        McpCapability.ARTIFACT_PLAN_SUBMIT,
    }:
        return evaluate_mapped_capability(normalized_capability, mapped_outcome)
    return AccessDecision.deny(
        "Unrecognized McpCapability "
        f"{normalized_capability.value!r}: ralph-workflow has not been updated "
        "to handle this capability variant",
        AccessDeniedCode.CAPABILITY_DENIED,
    )


__all__ = [
    "MCP_TO_RALPH_CAPABILITY_MAP",
    "AccessDecision",
    "AccessDeniedCode",
    "AccessMode",
    "Capability",
    "DrainClass",
    "McpCapability",
    "PolicyMode",
    "PolicyOutcome",
    "PolicyOutcomeStatus",
    "SessionDrain",
    "check_mcp_capability_policy",
    "drain_class_for_session",
    "drain_to_access_mode",
    "drain_to_policy_mode",
    "evaluate_mapped_capability",
    "evaluate_workspace_write",
    "lookup_ralph_capability",
    "policy_from_outcome",
]
