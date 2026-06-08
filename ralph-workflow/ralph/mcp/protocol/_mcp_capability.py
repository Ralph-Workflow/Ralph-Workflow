"""McpCapability — typed MCP capability vocabulary."""

from __future__ import annotations

from enum import StrEnum


class McpCapability(StrEnum):
    """Typed MCP capability vocabulary."""

    FILE_READ = "FileRead"
    FILE_WRITE = "FileWrite"
    GIT_READ = "GitRead"
    PROCESS_EXEC = "ProcessExec"
    ARTIFACT_SUBMIT = "ArtifactSubmit"
    ARTIFACT_PLAN_READ = "ArtifactPlanRead"
    ARTIFACT_PLAN_WRITE = "ArtifactPlanWrite"
    WORKSPACE_COORDINATION = "WorkspaceCoordination"
    WORKSPACE_READ = "WorkspaceRead"
    WORKSPACE_WRITE_EPHEMERAL = "WorkspaceWriteEphemeral"
    WORKSPACE_WRITE_TRACKED = "WorkspaceWriteTracked"
    WORKSPACE_WRITE_ANY = "WorkspaceWriteAny"
    WORKSPACE_METADATA_READ = "WorkspaceMetadataRead"
    WORKSPACE_EDIT = "WorkspaceEdit"
    WORKSPACE_DELETE = "WorkspaceDelete"
    GIT_STATUS_READ = "GitStatusRead"
    GIT_DIFF_READ = "GitDiffRead"
    GIT_WRITE = "GitWrite"
    ENV_READ = "EnvRead"
    ENV_WRITE = "EnvWrite"
    PROCESS_EXEC_BOUNDED = "ProcessExecBounded"
    PROCESS_EXEC_UNBOUNDED = "ProcessExecUnbounded"
    RUN_REPORT_PROGRESS = "RunReportProgress"
    UPSTREAM_TOOL_USE = "UpstreamToolUse"
    WEB_SEARCH = "WebSearch"
    WEB_VISIT = "WebVisit"
    WEB_DOWNLOAD = "WebDownload"
    MEDIA_READ = "MediaRead"
    ARTIFACT_PLAN_SUBMIT = "ArtifactPlanSubmit"


__all__ = ["McpCapability"]
