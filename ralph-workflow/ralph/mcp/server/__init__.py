from __future__ import annotations

from typing import TYPE_CHECKING

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


def __getattr__(name: str) -> object:
    if name in frozenset({'bound_dominates', 'classify_probe_failure', 'BridgeDiagnostics'}):
        from ralph.mcp.bridge_diagnostics import (
            BridgeDiagnostics as _v_BridgeDiagnostics,
        )
        from ralph.mcp.bridge_diagnostics import (
            bound_dominates as _v_bound_dominates,
        )
        from ralph.mcp.bridge_diagnostics import (
            classify_probe_failure as _v_classify_probe_failure,
        )
        if name == 'BridgeDiagnostics':
            return _v_BridgeDiagnostics
        elif name == 'bound_dominates':
            return _v_bound_dominates
        elif name == 'classify_probe_failure':
            return _v_classify_probe_failure

        raise AttributeError(name)
    if name in frozenset({'reset_subagent_sink', 'set_subagent_sink'}):
        from ._activity_sink import (
            reset_subagent_sink as _v_reset_subagent_sink,
        )
        from ._activity_sink import (
            set_subagent_sink as _v_set_subagent_sink,
        )
        if name == 'reset_subagent_sink':
            return _v_reset_subagent_sink
        elif name == 'set_subagent_sink':
            return _v_set_subagent_sink

        raise AttributeError(name)
    if name in frozenset({'ExitHandler'}):
        from ._exit_handler import (
            ExitHandler as _v_ExitHandler,
        )
        if name == 'ExitHandler':
            return _v_ExitHandler

        raise AttributeError(name)
    if name in frozenset({'describe_tool_set_diff', 'start_private_readiness_listener', 'compute_tool_set_hash', 'check_candidate_readiness', 'canonical_tool_entries', 'create_handover_socketpair'}):
        from ._handover_protocol import (
            canonical_tool_entries as _v_canonical_tool_entries,
        )
        from ._handover_protocol import (
            check_candidate_readiness as _v_check_candidate_readiness,
        )
        from ._handover_protocol import (
            compute_tool_set_hash as _v_compute_tool_set_hash,
        )
        from ._handover_protocol import (
            create_handover_socketpair as _v_create_handover_socketpair,
        )
        from ._handover_protocol import (
            describe_tool_set_diff as _v_describe_tool_set_diff,
        )
        from ._handover_protocol import (
            start_private_readiness_listener as _v_start_private_readiness_listener,
        )
        if name == 'canonical_tool_entries':
            return _v_canonical_tool_entries
        elif name == 'check_candidate_readiness':
            return _v_check_candidate_readiness
        elif name == 'compute_tool_set_hash':
            return _v_compute_tool_set_hash
        elif name == 'create_handover_socketpair':
            return _v_create_handover_socketpair
        elif name == 'describe_tool_set_diff':
            return _v_describe_tool_set_diff
        elif name == 'start_private_readiness_listener':
            return _v_start_private_readiness_listener

        raise AttributeError(name)
    if name in frozenset({'JsonRpcRequest'}):
        from ._json_rpc_request import (
            JsonRpcRequest as _v_JsonRpcRequest,
        )
        if name == 'JsonRpcRequest':
            return _v_JsonRpcRequest

        raise AttributeError(name)
    if name in frozenset({'read_durable_sink', 'configure_bridge_sink', 'clear_host_contention_signals', 'record_host_contention_signal'}):
        from ._mcp_health_event import (
            clear_host_contention_signals as _v_clear_host_contention_signals,
        )
        from ._mcp_health_event import (
            configure_bridge_sink as _v_configure_bridge_sink,
        )
        from ._mcp_health_event import (
            read_durable_sink as _v_read_durable_sink,
        )
        from ._mcp_health_event import (
            record_host_contention_signal as _v_record_host_contention_signal,
        )
        if name == 'clear_host_contention_signals':
            return _v_clear_host_contention_signals
        elif name == 'configure_bridge_sink':
            return _v_configure_bridge_sink
        elif name == 'read_durable_sink':
            return _v_read_durable_sink
        elif name == 'record_host_contention_signal':
            return _v_record_host_contention_signal

        raise AttributeError(name)
    if name in frozenset({'McpServer'}):
        from ._mcp_server import (
            McpServer as _v_McpServer,
        )
        if name == 'McpServer':
            return _v_McpServer

        raise AttributeError(name)
    if name in frozenset({'McpServerError'}):
        from ._mcp_server_error import (
            McpServerError as _v_McpServerError,
        )
        if name == 'McpServerError':
            return _v_McpServerError

        raise AttributeError(name)
    if name in frozenset({'get_default_metrics', 'reset_default_metrics'}):
        from ._metrics import (
            get_default_metrics as _v_get_default_metrics,
        )
        from ._metrics import (
            reset_default_metrics as _v_reset_default_metrics,
        )
        if name == 'get_default_metrics':
            return _v_get_default_metrics
        elif name == 'reset_default_metrics':
            return _v_reset_default_metrics

        raise AttributeError(name)
    if name in frozenset({'overrun_handler_override', 'exit_handler_override', 'stage_recorder_override'}):
        from ._pre_serve_overrun import (
            exit_handler_override as _v_exit_handler_override,
        )
        from ._pre_serve_overrun import (
            overrun_handler_override as _v_overrun_handler_override,
        )
        from ._pre_serve_overrun import (
            stage_recorder_override as _v_stage_recorder_override,
        )
        if name == 'exit_handler_override':
            return _v_exit_handler_override
        elif name == 'overrun_handler_override':
            return _v_overrun_handler_override
        elif name == 'stage_recorder_override':
            return _v_stage_recorder_override

        raise AttributeError(name)
    if name in frozenset({'StageEventRecorder'}):
        from ._pre_serve_overrun_events import (
            StageEventRecorder as _v_StageEventRecorder,
        )
        if name == 'StageEventRecorder':
            return _v_StageEventRecorder

        raise AttributeError(name)
    if name in frozenset({'ProcessLike'}):
        from ._process_like import (
            ProcessLike as _v_ProcessLike,
        )
        if name == 'ProcessLike':
            return _v_ProcessLike

        raise AttributeError(name)
    if name in frozenset({'submit_saturated_dispatch', 'reset_saturated_dispatch'}):
        from ._saturated_dispatch import (
            reset_saturated_dispatch as _v_reset_saturated_dispatch,
        )
        from ._saturated_dispatch import (
            submit_saturated_dispatch as _v_submit_saturated_dispatch,
        )
        if name == 'reset_saturated_dispatch':
            return _v_reset_saturated_dispatch
        elif name == 'submit_saturated_dispatch':
            return _v_submit_saturated_dispatch

        raise AttributeError(name)
    if name in frozenset({'session_warning_scope', 'session_warning_fired', 'session_before_warning'}):
        from ._session_wrapup import (
            session_before_warning as _v_session_before_warning,
        )
        from ._session_wrapup import (
            session_warning_fired as _v_session_warning_fired,
        )
        from ._session_wrapup import (
            session_warning_scope as _v_session_warning_scope,
        )
        if name == 'session_before_warning':
            return _v_session_before_warning
        elif name == 'session_warning_fired':
            return _v_session_warning_fired
        elif name == 'session_warning_scope':
            return _v_session_warning_scope

        raise AttributeError(name)
    if name in frozenset({'StageRecorder'}):
        from ._stage_recorder import (
            StageRecorder as _v_StageRecorder,
        )
        if name == 'StageRecorder':
            return _v_StageRecorder

        raise AttributeError(name)
    if name in frozenset({'failure_signature', 'TransportRepetitionTracker'}):
        from ._transport_repetition_tracker import (
            TransportRepetitionTracker as _v_TransportRepetitionTracker,
        )
        from ._transport_repetition_tracker import (
            failure_signature as _v_failure_signature,
        )
        if name == 'TransportRepetitionTracker':
            return _v_TransportRepetitionTracker
        elif name == 'failure_signature':
            return _v_failure_signature

        raise AttributeError(name)
    if name in frozenset({'verify_chain', 'append_wire_record', 'render_capture_table_markdown', 'WireLedgerIOError', 'wire_evidence_for', 'collect_captures'}):
        from ._wire_ledger import (
            WireLedgerIOError as _v_WireLedgerIOError,
        )
        from ._wire_ledger import (
            append_wire_record as _v_append_wire_record,
        )
        from ._wire_ledger import (
            collect_captures as _v_collect_captures,
        )
        from ._wire_ledger import (
            render_capture_table_markdown as _v_render_capture_table_markdown,
        )
        from ._wire_ledger import (
            verify_chain as _v_verify_chain,
        )
        from ._wire_ledger import (
            wire_evidence_for as _v_wire_evidence_for,
        )
        if name == 'WireLedgerIOError':
            return _v_WireLedgerIOError
        elif name == 'append_wire_record':
            return _v_append_wire_record
        elif name == 'collect_captures':
            return _v_collect_captures
        elif name == 'render_capture_table_markdown':
            return _v_render_capture_table_markdown
        elif name == 'verify_chain':
            return _v_verify_chain
        elif name == 'wire_evidence_for':
            return _v_wire_evidence_for

        raise AttributeError(name)
    if name in frozenset({'factory_impl'}):
        from .factory_impl import (
            factory_impl as _v_factory_impl,
        )
        if name == 'factory_impl':
            return _v_factory_impl

        raise AttributeError(name)
    if name in frozenset({'SHUTDOWN_REAP_WAIT_S', 'RestartAwareMcpBridge', 'SHUTDOWN_TERMINATE_GRACE_S', 'SessionBridgeLike', 'McpServerExtras', 'shutdown_mcp_server', 'McpRestartPolicy', 'start_mcp_server', 'StandaloneMcpProcess', 'startup_diagnostic_suffix'}):
        from .lifecycle import (
            SHUTDOWN_REAP_WAIT_S as _v_SHUTDOWN_REAP_WAIT_S,
        )
        from .lifecycle import (
            SHUTDOWN_TERMINATE_GRACE_S as _v_SHUTDOWN_TERMINATE_GRACE_S,
        )
        from .lifecycle import (
            McpRestartPolicy as _v_McpRestartPolicy,
        )
        from .lifecycle import (
            McpServerExtras as _v_McpServerExtras,
        )
        from .lifecycle import (
            RestartAwareMcpBridge as _v_RestartAwareMcpBridge,
        )
        from .lifecycle import (
            SessionBridgeLike as _v_SessionBridgeLike,
        )
        from .lifecycle import (
            StandaloneMcpProcess as _v_StandaloneMcpProcess,
        )
        from .lifecycle import (
            shutdown_mcp_server as _v_shutdown_mcp_server,
        )
        from .lifecycle import (
            start_mcp_server as _v_start_mcp_server,
        )
        from .lifecycle import (
            startup_diagnostic_suffix as _v_startup_diagnostic_suffix,
        )
        if name == 'McpRestartPolicy':
            return _v_McpRestartPolicy
        elif name == 'McpServerExtras':
            return _v_McpServerExtras
        elif name == 'RestartAwareMcpBridge':
            return _v_RestartAwareMcpBridge
        elif name == 'SHUTDOWN_REAP_WAIT_S':
            return _v_SHUTDOWN_REAP_WAIT_S
        elif name == 'SHUTDOWN_TERMINATE_GRACE_S':
            return _v_SHUTDOWN_TERMINATE_GRACE_S
        elif name == 'SessionBridgeLike':
            return _v_SessionBridgeLike
        elif name == 'StandaloneMcpProcess':
            return _v_StandaloneMcpProcess
        elif name == 'shutdown_mcp_server':
            return _v_shutdown_mcp_server
        elif name == 'start_mcp_server':
            return _v_start_mcp_server
        elif name == 'startup_diagnostic_suffix':
            return _v_startup_diagnostic_suffix

        raise AttributeError(name)
    if name in frozenset({'run_standalone_server'}):
        from .runtime import (
            run_standalone_server as _v_run_standalone_server,
        )
        if name == 'run_standalone_server':
            return _v_run_standalone_server

        raise AttributeError(name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
