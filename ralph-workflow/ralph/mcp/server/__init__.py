"""Standalone MCP server exports.

This package separates standalone server startup/shutdown helpers from the rest
of the bridge implementation so callers can either launch an in-process session
bridge or run the dedicated ``ralph-mcp`` HTTP runtime.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from ._activity_sink import reset_subagent_sink, set_subagent_sink

if TYPE_CHECKING:
    from .lifecycle import McpServerExtras, SessionBridgeLike, shutdown_mcp_server, start_mcp_server
    from .runtime import run_standalone_server

__all__ = [
    "BridgeDiagnostics",
    "HandoverTiming",
    "McpRestartPolicy",
    "McpServerError",
    "McpServerExtras",
    "RestartAwareMcpBridge",
    "SessionBridgeLike",
    "StandaloneMcpProcess",
    "append_wire_record",
    "bound_dominates",
    "canonical_tool_entries",
    "check_candidate_readiness",
    "classify_probe_failure",
    "clear_host_contention_signals",
    "collect_captures",
    "compute_tool_set_hash",
    "configure_bridge_sink",
    "create_handover_socketpair",
    "describe_tool_set_diff",
    "factory_impl",
    "failure_signature",
    "get_default_metrics",
    "read_durable_sink",
    "record_host_contention_signal",
    "render_capture_table_markdown",
    "reset_default_metrics",
    "reset_saturated_dispatch",
    "reset_subagent_sink",
    "run_standalone_server",
    "session_before_warning",
    "session_warning_fired",
    "session_warning_scope",
    "set_subagent_sink",
    "shutdown_mcp_server",
    "start_mcp_server",
    "start_private_readiness_listener",
    "startup_diagnostic_suffix",
    "submit_saturated_dispatch",
    "verify_chain",
    "wire_evidence_for",
]


_LAZY_MODULES = {
    "BridgeDiagnostics": "ralph.mcp.bridge_diagnostics",
    "HandoverTiming": "ralph.mcp.bridge_handover",
    "McpRestartPolicy": "ralph.mcp.server.lifecycle",
    "McpServerError": "ralph.mcp.server._mcp_server_error",
    "McpServerExtras": "ralph.mcp.server.lifecycle",
    "RestartAwareMcpBridge": "ralph.mcp.server.lifecycle",
    "SessionBridgeLike": "ralph.mcp.server.lifecycle",
    "StandaloneMcpProcess": "ralph.mcp.server.lifecycle",
    "append_wire_record": "ralph.mcp.server._wire_ledger",
    "bound_dominates": "ralph.mcp.bridge_diagnostics",
    "canonical_tool_entries": "ralph.mcp.server._handover_protocol",
    "check_candidate_readiness": "ralph.mcp.server._handover_protocol",
    "classify_probe_failure": "ralph.mcp.bridge_diagnostics",
    "clear_host_contention_signals": "ralph.mcp.server._mcp_health_event",
    "collect_captures": "ralph.mcp.server._wire_ledger",
    "compute_tool_set_hash": "ralph.mcp.server._handover_protocol",
    "configure_bridge_sink": "ralph.mcp.server._mcp_health_event",
    "create_handover_socketpair": "ralph.mcp.server._handover_protocol",
    "describe_tool_set_diff": "ralph.mcp.server._handover_protocol",
    "factory_impl": "ralph.mcp.server.factory_impl",
    "failure_signature": "ralph.mcp.server._transport_repetition_tracker",
    "get_default_metrics": "ralph.mcp.server._metrics",
    "read_durable_sink": "ralph.mcp.server._mcp_health_event",
    "record_host_contention_signal": "ralph.mcp.server._mcp_health_event",
    "render_capture_table_markdown": "ralph.mcp.server._wire_ledger",
    "reset_default_metrics": "ralph.mcp.server._metrics",
    "reset_saturated_dispatch": "ralph.mcp.server._saturated_dispatch",
    "run_standalone_server": "ralph.mcp.server.runtime",
    "session_before_warning": "ralph.mcp.server._session_wrapup",
    "session_warning_fired": "ralph.mcp.server._session_wrapup",
    "session_warning_scope": "ralph.mcp.server._session_wrapup",
    "set_subagent_sink": "ralph.mcp.server._activity_sink",
    "shutdown_mcp_server": "ralph.mcp.server.lifecycle",
    "start_mcp_server": "ralph.mcp.server.lifecycle",
    "start_private_readiness_listener": "ralph.mcp.server._handover_protocol",
    "startup_diagnostic_suffix": "ralph.mcp.server.lifecycle",
    "submit_saturated_dispatch": "ralph.mcp.server._saturated_dispatch",
    "verify_chain": "ralph.mcp.server._wire_ledger",
    "wire_evidence_for": "ralph.mcp.server._wire_ledger",
}


def __getattr__(name: str) -> object:
    target = _LAZY_MODULES.get(name)
    if target is not None:
        # Lazy: an eager lifecycle import here creates a cycle
        # (upstream.config -> server._mcp_server_error -> package init).
        return cast("object", getattr(import_module(target), name))
    try:
        return import_module(f"ralph.mcp.server.{name}")
    except ImportError:
        pass
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
