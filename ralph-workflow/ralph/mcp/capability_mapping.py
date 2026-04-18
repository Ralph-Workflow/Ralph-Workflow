"""MCP capability mapping for Ralph sessions.

Ports the Rust capability-mapping layer used to translate session drain and
policy outcomes into MCP access-control decisions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import cast


class SessionDrain(StrEnum):
    """Pipeline drain identity for a Ralph session."""

    PLANNING = "planning"
    DEVELOPMENT = "development"
    DEVELOPMENT_ANALYSIS = "development_analysis"
    DEVELOPMENT_COMMIT = "development_commit"
    ANALYSIS = "analysis"
    REVIEW = "review"
    REVIEW_ANALYSIS = "review_analysis"
    REVIEW_COMMIT = "review_commit"
    FIX = "fix"
    COMMIT = "commit"


class DrainClass(StrEnum):
    """Drain class used for capability defaults."""

    PLANNING = "planning"
    DEVELOPMENT = "development"
    ANALYSIS = "analysis"
    REVIEW = "review"
    FIX = "fix"
    COMMIT = "commit"

    def allows_write(self) -> bool:
        """Return whether this drain class allows write operations."""
        return self in {DrainClass.DEVELOPMENT, DrainClass.FIX}


class AccessMode(StrEnum):
    """Server access mode for MCP tool dispatch."""

    READ_ONLY = "ReadOnly"
    READ_WRITE = "ReadWrite"

    def allows_write(self) -> bool:
        """Return whether this access mode allows write operations."""
        return self is AccessMode.READ_WRITE


class PolicyMode(StrEnum):
    """Runtime policy mode enforced by the MCP server."""

    PLANNING = "planning"
    DEVELOPMENT = "development"
    ANALYSIS = "analysis"
    REVIEW = "review"
    FIX = "fix"
    COMMIT = "commit"

    def access_mode(self) -> AccessMode:
        """Return the matching access mode."""
        if self in {PolicyMode.DEVELOPMENT, PolicyMode.FIX}:
            return AccessMode.READ_WRITE
        return AccessMode.READ_ONLY


class AccessDeniedCode(StrEnum):
    """Categorical access-denial codes."""

    NOT_INITIALIZED = "NotInitialized"
    CAPABILITY_DENIED = "CapabilityDenied"
    READ_ONLY_MODE = "ReadOnlyMode"
    OUTSIDE_ROOT_DIR = "OutsideRootDir"
    TOOL_NOT_ALLOWED = "ToolNotAllowed"


@dataclass(frozen=True)
class AccessDecision:
    """Result of an MCP access decision."""

    allowed: bool
    reason: str | None = None
    code: AccessDeniedCode | None = None

    @classmethod
    def allow(cls) -> AccessDecision:
        """Build an allow decision."""
        return cls(allowed=True)

    @classmethod
    def deny(cls, reason: str, code: AccessDeniedCode) -> AccessDecision:
        """Build a deny decision."""
        return cls(allowed=False, reason=reason, code=code)

    def is_allowed(self) -> bool:
        """Return whether access is allowed."""
        return self.allowed


class Capability(StrEnum):
    """Internal Ralph capability vocabulary."""

    WORKSPACE_READ = "workspace.read"
    WORKSPACE_WRITE_EPHEMERAL = "workspace.write_ephemeral"
    WORKSPACE_WRITE_TRACKED = "workspace.write_tracked"
    PROCESS_EXEC_BOUNDED = "process.exec_bounded"
    PROCESS_EXEC_UNBOUNDED = "process.exec_unbounded"
    ARTIFACT_SUBMIT = "artifact.submit"
    RUN_REPORT_PROGRESS = "run.report_progress"
    GIT_STATUS_READ = "git.status_read"
    GIT_DIFF_READ = "git.diff_read"
    GIT_WRITE = "git.write"
    ENV_READ = "env.read"
    ENV_WRITE = "env.write"
    UPSTREAM_TOOL_USE = "upstream.tool_use"


class McpCapability(StrEnum):
    """Typed MCP capability vocabulary."""

    FILE_READ = "FileRead"
    FILE_WRITE = "FileWrite"
    GIT_READ = "GitRead"
    PROCESS_EXEC = "ProcessExec"
    ARTIFACT_SUBMIT = "ArtifactSubmit"
    WORKSPACE_COORDINATION = "WorkspaceCoordination"
    WORKSPACE_READ = "WorkspaceRead"
    WORKSPACE_WRITE_EPHEMERAL = "WorkspaceWriteEphemeral"
    WORKSPACE_WRITE_TRACKED = "WorkspaceWriteTracked"
    WORKSPACE_WRITE_ANY = "WorkspaceWriteAny"
    GIT_STATUS_READ = "GitStatusRead"
    GIT_WRITE = "GitWrite"
    ENV_READ = "EnvRead"
    ENV_WRITE = "EnvWrite"
    PROCESS_EXEC_BOUNDED = "ProcessExecBounded"
    PROCESS_EXEC_UNBOUNDED = "ProcessExecUnbounded"
    RUN_REPORT_PROGRESS = "RunReportProgress"
    UPSTREAM_TOOL_USE = "UpstreamToolUse"


class PolicyOutcomeStatus(StrEnum):
    """Normalized policy outcome status."""

    APPROVED = "approved"
    DENIED = "denied"
    APPROVED_WITH_RESTRICTION = "approved_with_restriction"


@dataclass(frozen=True)
class PolicyOutcome:
    """Normalized policy outcome payload."""

    status: PolicyOutcomeStatus
    reason: str | None = None
    restriction: str | None = None


MCP_TO_RALPH_CAPABILITY_MAP: dict[McpCapability, Capability] = {
    McpCapability.FILE_READ: Capability.WORKSPACE_READ,
    McpCapability.GIT_READ: Capability.GIT_STATUS_READ,
    McpCapability.PROCESS_EXEC: Capability.PROCESS_EXEC_BOUNDED,
    McpCapability.ARTIFACT_SUBMIT: Capability.ARTIFACT_SUBMIT,
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
}

_MCP_CAPABILITY_ALIASES: dict[str, McpCapability] = {
    "workspace.read": McpCapability.WORKSPACE_READ,
    "workspace.write_ephemeral": McpCapability.WORKSPACE_WRITE_EPHEMERAL,
    "workspace.write_tracked": McpCapability.WORKSPACE_WRITE_TRACKED,
    "artifact.submit": McpCapability.ARTIFACT_SUBMIT,
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
}

_APPROVED_POLICY_VALUES = {"approved", "allow", "allowed"}
_DENIED_POLICY_VALUES = {"denied", "deny", "denied_by_policy"}
_APPROVED_WITH_RESTRICTION_VALUES = {
    "approvedwithrestriction",
    "approved_with_restriction",
    "allow_with_restriction",
    "allowed_with_restriction",
}


def _normalize_token(value: str) -> str:
    return value.strip().replace("-", "_").replace(" ", "_").lower()


def _extract_text_field(value: object, field_name: str) -> str | None:
    if isinstance(value, dict):
        field_value = value.get(field_name)
    else:
        field_value = getattr(value, field_name, None)
    return field_value if isinstance(field_value, str) else None


def _extract_named_value(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, Enum):
        enum_value = cast("object", value.value)
        if isinstance(enum_value, str):
            return enum_value
    for field_name in ("status", "name", "value"):
        field_value = _extract_text_field(value, field_name)
        if field_value is not None:
            return field_value
    return None


def _coerce_session_drain(value: SessionDrain | str) -> SessionDrain:
    if isinstance(value, SessionDrain):
        return value

    normalized = _normalize_token(value)
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


def _coerce_capability(value: Capability | str) -> Capability:
    if isinstance(value, Capability):
        return value

    normalized = _normalize_token(value)
    for capability in Capability:
        if _normalize_token(capability.value) == normalized:
            return capability

    try:
        return _RALPH_CAPABILITY_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown Ralph capability: {value!r}") from exc


def _coerce_mcp_capability(value: McpCapability | str) -> McpCapability:
    if isinstance(value, McpCapability):
        return value

    normalized = _normalize_token(value)
    for capability in McpCapability:
        if _normalize_token(capability.value) == normalized:
            return capability

    try:
        return _MCP_CAPABILITY_ALIASES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unknown McpCapability: {value!r}") from exc


def _normalize_policy_outcome(value: object) -> PolicyOutcome:
    if isinstance(value, PolicyOutcome):
        return value
    if value is True:
        return PolicyOutcome(status=PolicyOutcomeStatus.APPROVED)

    status_value = _extract_named_value(value)
    normalized_status = _normalize_token(status_value) if status_value is not None else ""
    reason = _extract_text_field(value, "reason")
    restriction = _extract_text_field(value, "restriction")

    status = _resolved_policy_status(value, normalized_status, reason)
    if status is not None:
        return PolicyOutcome(status=status, reason=reason, restriction=restriction)

    raise ValueError(f"Unsupported policy outcome: {value!r}")


def _resolved_policy_status(
    value: object,
    normalized_status: str,
    reason: str | None,
) -> PolicyOutcomeStatus | None:
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


def drain_class_for_session(drain: SessionDrain | str) -> DrainClass:
    """Classify a session drain into its drain class."""
    session_drain = _coerce_session_drain(drain)
    mapping: dict[SessionDrain, DrainClass] = {
        SessionDrain.PLANNING: DrainClass.PLANNING,
        SessionDrain.DEVELOPMENT: DrainClass.DEVELOPMENT,
        SessionDrain.DEVELOPMENT_ANALYSIS: DrainClass.ANALYSIS,
        SessionDrain.REVIEW_ANALYSIS: DrainClass.ANALYSIS,
        SessionDrain.DEVELOPMENT_COMMIT: DrainClass.COMMIT,
        SessionDrain.REVIEW_COMMIT: DrainClass.COMMIT,
        SessionDrain.ANALYSIS: DrainClass.ANALYSIS,
        SessionDrain.REVIEW: DrainClass.REVIEW,
        SessionDrain.FIX: DrainClass.FIX,
        SessionDrain.COMMIT: DrainClass.COMMIT,
    }
    return mapping[session_drain]


def drain_to_access_mode(drain: SessionDrain | str) -> AccessMode:
    """Determine the MCP access mode for a session drain."""
    if drain_class_for_session(drain).allows_write():
        return AccessMode.READ_WRITE
    return AccessMode.READ_ONLY


def drain_to_policy_mode(drain: SessionDrain | str) -> PolicyMode:
    """Map a session drain to the matching policy mode."""
    session_drain = _coerce_session_drain(drain)
    mapping: dict[SessionDrain, PolicyMode] = {
        SessionDrain.PLANNING: PolicyMode.PLANNING,
        SessionDrain.DEVELOPMENT: PolicyMode.DEVELOPMENT,
        SessionDrain.DEVELOPMENT_ANALYSIS: PolicyMode.ANALYSIS,
        SessionDrain.REVIEW_ANALYSIS: PolicyMode.ANALYSIS,
        SessionDrain.DEVELOPMENT_COMMIT: PolicyMode.COMMIT,
        SessionDrain.REVIEW_COMMIT: PolicyMode.COMMIT,
        SessionDrain.ANALYSIS: PolicyMode.ANALYSIS,
        SessionDrain.REVIEW: PolicyMode.REVIEW,
        SessionDrain.FIX: PolicyMode.FIX,
        SessionDrain.COMMIT: PolicyMode.COMMIT,
    }
    return mapping[session_drain]


def lookup_ralph_capability(capability: McpCapability | str) -> Capability | None:
    """Look up the Ralph capability mapped from an MCP capability."""
    try:
        normalized_capability = _coerce_mcp_capability(capability)
    except ValueError:
        return None
    return MCP_TO_RALPH_CAPABILITY_MAP.get(normalized_capability)


def policy_from_outcome(outcome: object) -> AccessDecision:
    """Convert a Ralph policy outcome to an MCP access decision."""
    normalized_outcome = _normalize_policy_outcome(outcome)
    if normalized_outcome.status in {
        PolicyOutcomeStatus.APPROVED,
        PolicyOutcomeStatus.APPROVED_WITH_RESTRICTION,
    }:
        return AccessDecision.allow()

    reason = normalized_outcome.reason or "Capability denied"
    return AccessDecision.deny(reason, AccessDeniedCode.CAPABILITY_DENIED)


def evaluate_workspace_write(ephemeral: object, tracked: object) -> AccessDecision:
    """Evaluate the composite workspace-write policy."""
    ephemeral_outcome = _normalize_policy_outcome(ephemeral)
    tracked_outcome = _normalize_policy_outcome(tracked)
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
        normalized_capability = _coerce_mcp_capability(capability)
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
    _coerce_capability(mapped_capability)
    return policy_from_outcome(outcome)


def check_mcp_capability_policy(
    capability: McpCapability | str,
    ephemeral: object,
    tracked: object,
    mapped_outcome: tuple[Capability | str, object] | None,
) -> AccessDecision:
    """Decide access for an MCP capability from session policy outcomes."""
    try:
        normalized_capability = _coerce_mcp_capability(capability)
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
