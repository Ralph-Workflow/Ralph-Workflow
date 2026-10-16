"""Shared Ralph MCP environment variable names."""

from __future__ import annotations

from enum import StrEnum


class McpEnvVar(StrEnum):
    """Environment variable names used to configure the Ralph MCP connection."""

    ENDPOINT = "RALPH_MCP_ENDPOINT"
    RUN_ID = "RALPH_MCP_RUN_ID"
    AGENT_LABEL_SCOPE = "RALPH_AGENT_LABEL_SCOPE"
    SESSION = "RALPH_MCP_SESSION_JSON"
    SESSION_FILE = "RALPH_MCP_SESSION_FILE"
    PREFLIGHT_TIMEOUT_MS = "RALPH_MCP_PREFLIGHT_TIMEOUT_MS"
    UPSTREAM_PROBE_TIMEOUT_MS = "RALPH_MCP_UPSTREAM_PROBE_TIMEOUT_MS"
    SUPERVISION_INTERVAL_MS = "RALPH_MCP_SUPERVISION_INTERVAL_MS"
    PROBE_TIMEOUT_MS = "RALPH_MCP_PROBE_TIMEOUT_MS"
    WORKER_ARTIFACT_DIR = "RALPH_WORKER_ARTIFACT_DIR"
    WORKER_ID = "RALPH_WORKER_ID"
    WORKER_NAMESPACE = "RALPH_WORKER_NAMESPACE"
    PARALLEL_WORKER_MANIFEST = "RALPH_PARALLEL_WORKER_MANIFEST"
    MAX_SESSION_SECONDS = "RALPH_MAX_SESSION_SECONDS"
    # Wall-clock epochs, not durations: the cycle deadline is fixed for the
    # lifetime of one agent invocation, and its artifact validator reads them
    # against its own wall clock.
    CYCLE_WARN_EPOCH = "RALPH_CYCLE_WARN_EPOCH"
    CYCLE_DEADLINE_EPOCH = "RALPH_CYCLE_DEADLINE_EPOCH"
    # Independent development-phase warning/deadline epochs. These must not
    # reuse cycle names: both timers can be active for the same invocation.
    DEV_WARN_EPOCH = "RALPH_DEV_WARN_EPOCH"
    DEV_DEADLINE_EPOCH = "RALPH_DEV_DEADLINE_EPOCH"
    # PID of the Ralph process that spawned this MCP server; the server exits
    # once it is reparented away from it instead of running orphaned.
    OWNER_PID = "RALPH_MCP_OWNER_PID"
    REQUEST_TIMEOUT_MS = "RALPH_MCP_REQUEST_TIMEOUT_MS"
    # Inherited-FD handoff for the parent-owned listening socket: the
    # lifecycle reserves the port once, the child adopts the FD by
    # number, and the bridge inherits the same socket across respawns
    # so a kernel backlog (not a refused connection) is what an
    # in-flight client sees between respawns.
    INHERITED_FD = "RALPH_MCP_INHERITED_FD"
    # Respawn sentinel: every child beyond the first runs with this
    # flag set. The runtime reads it to take the catalog-only
    # ``UpstreamRegistry.build_from_tool_catalog`` path so a respawn
    # does zero network I/O before serving. The flag is absent for
    # the first start, so first-start discovery still probes
    # configured upstreams once and caches the catalog in the parent.
    RESPAWN = "RALPH_MCP_RESPAWN"
    # Per-spawn stage identity: the parent generates one UUID per
    # spawn and hands it to the child via this env var. The child
    # uses it as the stage-file name suffix so the parent and the
    # child address the same file across the spawn boundary — the
    # module-level UUID at import time would otherwise give each
    # side a different file and the parent's ``read_last_stage``
    # would always return ``None`` after a preflight failure.
    STAGE_ID = "RALPH_MCP_STAGE_ID"


MCP_ENDPOINT_ENV = McpEnvVar.ENDPOINT
MCP_RUN_ID_ENV = McpEnvVar.RUN_ID
AGENT_LABEL_SCOPE_ENV = McpEnvVar.AGENT_LABEL_SCOPE
MCP_SESSION_ENV = McpEnvVar.SESSION
MCP_SESSION_FILE_ENV = McpEnvVar.SESSION_FILE
MCP_OWNER_PID_ENV = McpEnvVar.OWNER_PID
MCP_PREFLIGHT_TIMEOUT_MS_ENV = McpEnvVar.PREFLIGHT_TIMEOUT_MS
MCP_UPSTREAM_PROBE_TIMEOUT_MS_ENV = McpEnvVar.UPSTREAM_PROBE_TIMEOUT_MS
MCP_SUPERVISION_INTERVAL_MS_ENV = McpEnvVar.SUPERVISION_INTERVAL_MS
MCP_PROBE_TIMEOUT_MS_ENV = McpEnvVar.PROBE_TIMEOUT_MS
WORKER_ARTIFACT_DIR_ENV = McpEnvVar.WORKER_ARTIFACT_DIR
WORKER_ID_ENV = McpEnvVar.WORKER_ID
WORKER_NAMESPACE_ENV = McpEnvVar.WORKER_NAMESPACE
RALPH_PARALLEL_WORKER_MANIFEST_ENV = McpEnvVar.PARALLEL_WORKER_MANIFEST
MAX_SESSION_SECONDS_ENV = McpEnvVar.MAX_SESSION_SECONDS
MCP_REQUEST_TIMEOUT_MS_ENV = McpEnvVar.REQUEST_TIMEOUT_MS
CYCLE_WARN_EPOCH_ENV = McpEnvVar.CYCLE_WARN_EPOCH
CYCLE_DEADLINE_EPOCH_ENV = McpEnvVar.CYCLE_DEADLINE_EPOCH
DEV_WARN_EPOCH_ENV = McpEnvVar.DEV_WARN_EPOCH
DEV_DEADLINE_EPOCH_ENV = McpEnvVar.DEV_DEADLINE_EPOCH
MCP_INHERITED_FD_ENV = McpEnvVar.INHERITED_FD
MCP_RESPAWN_ENV = McpEnvVar.RESPAWN
MCP_STAGE_ID_ENV = McpEnvVar.STAGE_ID
