"""Standalone MCP server exports.

This package separates standalone server startup/shutdown helpers from the rest
of the bridge implementation so callers can either launch an in-process session
bridge or run the dedicated ``ralph-mcp`` HTTP runtime.

The bounded pre-serve startup seams are part of that public surface. The stage
recorder, the exit handler, and the overrun handler are the INJECTION points a
caller uses to observe the startup stage sequence and to replace the
fail-closed process-exit boundary; the ``*_override`` context managers scope
one registration to one startup. They are exported here so a caller never has
to reach into a private module to use them.
"""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, cast

from ._activity_sink import reset_subagent_sink, set_subagent_sink

if TYPE_CHECKING:
    from ._exit_handler import ExitHandler
    from ._json_rpc_request import JsonRpcRequest
    from ._mcp_server import McpServer
    from ._mcp_server_error import McpServerError
    from ._pre_serve_overrun import (
        StageEventRecorder,
        exit_handler_override,
        overrun_handler_override,
        stage_recorder_override,
    )
    from ._process_like import ProcessLike
    from ._stage_recorder import StageRecorder
    from ._standalone_mcp_process import SHUTDOWN_REAP_WAIT_S, SHUTDOWN_TERMINATE_GRACE_S
    from ._transport_repetition_tracker import TransportRepetitionTracker
    from ._wire_ledger import WireLedgerIOError
    from .lifecycle import McpServerExtras, SessionBridgeLike, shutdown_mcp_server, start_mcp_server
    from .runtime import run_standalone_server

__all__ = [
    "SHUTDOWN_REAP_WAIT_S",
    "SHUTDOWN_TERMINATE_GRACE_S",
    "BridgeDiagnostics",
    "ExitHandler",
    "JsonRpcRequest",
    "McpRestartPolicy",
    "McpServer",
    "McpServerError",
    "McpServerExtras",
    "ProcessLike",
    "RestartAwareMcpBridge",
    "SessionBridgeLike",
    "StageEventRecorder",
    "StageRecorder",
    "StandaloneMcpProcess",
    "TransportRepetitionTracker",
    "WireLedgerIOError",
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
    "exit_handler_override",
    "factory_impl",
    "failure_signature",
    "get_default_metrics",
    "overrun_handler_override",
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
    "stage_recorder_override",
    "start_mcp_server",
    "start_private_readiness_listener",
    "startup_diagnostic_suffix",
    "submit_saturated_dispatch",
    "verify_chain",
    "wire_evidence_for",
]


_LAZY_MODULES = {
    "BridgeDiagnostics": "ralph.mcp.bridge_diagnostics",
    "ExitHandler": "ralph.mcp.server._exit_handler",
    "JsonRpcRequest": "ralph.mcp.server._json_rpc_request",
    "McpRestartPolicy": "ralph.mcp.server.lifecycle",
    "McpServer": "ralph.mcp.server._mcp_server",
    "McpServerError": "ralph.mcp.server._mcp_server_error",
    "McpServerExtras": "ralph.mcp.server.lifecycle",
    "ProcessLike": "ralph.mcp.server._process_like",
    "RestartAwareMcpBridge": "ralph.mcp.server.lifecycle",
    "SHUTDOWN_REAP_WAIT_S": "ralph.mcp.server.lifecycle",
    "SHUTDOWN_TERMINATE_GRACE_S": "ralph.mcp.server.lifecycle",
    "SessionBridgeLike": "ralph.mcp.server.lifecycle",
    "StageEventRecorder": "ralph.mcp.server._pre_serve_overrun_events",
    "StageRecorder": "ralph.mcp.server._stage_recorder",
    "StandaloneMcpProcess": "ralph.mcp.server.lifecycle",
    "TransportRepetitionTracker": "ralph.mcp.server._transport_repetition_tracker",
    "WireLedgerIOError": "ralph.mcp.server._wire_ledger",
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
    "exit_handler_override": "ralph.mcp.server._pre_serve_overrun",
    "factory_impl": "ralph.mcp.server.factory_impl",
    "failure_signature": "ralph.mcp.server._transport_repetition_tracker",
    "get_default_metrics": "ralph.mcp.server._metrics",
    "overrun_handler_override": "ralph.mcp.server._pre_serve_overrun",
    "read_durable_sink": "ralph.mcp.server._mcp_health_event",
    "record_host_contention_signal": "ralph.mcp.server._mcp_health_event",
    "render_capture_table_markdown": "ralph.mcp.server._wire_ledger",
    "reset_default_metrics": "ralph.mcp.server._metrics",
    "reset_saturated_dispatch": "ralph.mcp.server._saturated_dispatch",
    "reset_subagent_sink": "ralph.mcp.server._activity_sink",
    "run_standalone_server": "ralph.mcp.server.runtime",
    "session_before_warning": "ralph.mcp.server._session_wrapup",
    "session_warning_fired": "ralph.mcp.server._session_wrapup",
    "session_warning_scope": "ralph.mcp.server._session_wrapup",
    "set_subagent_sink": "ralph.mcp.server._activity_sink",
    "shutdown_mcp_server": "ralph.mcp.server.lifecycle",
    "stage_recorder_override": "ralph.mcp.server._pre_serve_overrun",
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
    submodule = f"{__name__}.{name}"
    try:
        return import_module(submodule)
    except ModuleNotFoundError as exc:
        # Only "this submodule does not exist" means "no such attribute". A
        # submodule that exists but fails to import (a circular import, a
        # missing dependency of its own) must surface, not become an
        # AttributeError that hides the real cause.
        if exc.name != submodule:
            raise
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
