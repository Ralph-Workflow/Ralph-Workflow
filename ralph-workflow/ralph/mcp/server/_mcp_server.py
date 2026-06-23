"""Lightweight MCP server implementation for the fallback HTTP runtime."""

from __future__ import annotations

import base64 as _base64
import json
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph import __version__
from ralph.agents.system_clock import SystemClock
from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.multimodal.resources import parse_media_uri
from ralph.mcp.server._activity_sink import get_active_sink, invoke_active_sink
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._metrics import McpMetrics, get_default_metrics
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._session_wrapup import SessionWrapupBudget
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
    ToolResult,
)
from ralph.mcp.tools.json_repair import repair_json_containers
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME, RalphToolName, claude_tool_name
from ralph.timeout_defaults import MAX_SESSION_SECONDS, SESSION_SOFT_WRAPUP_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.protocol.session import McpSession
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.tools.bridge import ToolBridge
    from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from typing import Protocol

    class _ToDict(Protocol):
        def __call__(self) -> dict[str, object]: ...

    class _ModelDump(Protocol):
        def __call__(self, **kwargs: bool) -> dict[str, object]: ...


# Import-time invariant: every RalphToolName alias must be a non-degenerate
# mcp__<server>__<tool> name. The whole point of exposing aliases in
# tools/list is that strict-MCP clients (e.g. Claude Code) only accept the
# `mcp__<server>__<tool>` form. If `claude_tool_name(name) == name` for any
# member, the alias emission rule in `_handle_tools_list` becomes a no-op
# and the live failure mode (Claude attempts `mcp__<server>__<tool>` and the
# server rejects it) reappears. Fail loudly with a RuntimeError (NOT
# `assert`) so the invariant survives `python -O`.
for _member in RalphToolName:
    _alias = claude_tool_name(_member)
    if _alias == str(_member):
        raise RuntimeError(
            f"claude_tool_name({_member!r}) degenerated to its raw name; "
            "alias emission in _handle_tools_list would be a no-op"
        )


def _serialize_content_blocks(content_blocks: object) -> list[dict[str, object]]:
    if not isinstance(content_blocks, list | tuple):
        raise TypeError(
            f"content_blocks must be a list or tuple, got {type(content_blocks).__name__}. "
            "Use ToolContent.text_content() or ImageContent() to wrap content."
        )

    serialized: list[dict[str, object]] = []
    blocks = cast("list[object]", content_blocks)
    for idx, block in enumerate(blocks):
        if isinstance(block, dict):
            serialized.append(cast("dict[str, object]", block))
            continue

        to_dict = cast("_ToDict | None", getattr(block, "to_dict", None))
        if callable(to_dict):
            serialized.append(to_dict())
            continue

        model_dump = cast("_ModelDump | None", getattr(block, "model_dump", None))
        if callable(model_dump):
            serialized.append(model_dump(exclude_none=True, by_alias=True))
            continue

        raise TypeError(
            f"Unsupported content block type at index {idx}: "
            f"{type(block).__name__}. "
            "Content blocks must be dict, ToolContent, ImageContent, or a Pydantic model "
            "with to_dict() or model_dump() methods."
        )

    return serialized


def _decode_json_payload_from_content(content_blocks: object) -> dict[str, object] | None:
    serialized = _serialize_content_blocks(content_blocks)
    if not serialized:
        return None
    first = serialized[0]
    text = first.get("text")
    if not isinstance(text, str):
        return None
    try:
        decoded = cast("object", json.loads(text))
    except json.JSONDecodeError:
        return None
    if not isinstance(decoded, dict):
        return None
    if "content" not in decoded:
        return None
    return cast("dict[str, object]", decoded)


def _extract_client_capabilities(params: dict[str, object] | None) -> set[str]:
    if not params:
        return set()

    capabilities: object = params.get("capabilities", {})
    if not isinstance(capabilities, dict):
        return set()

    result: set[str] = set()

    for key in capabilities:
        if key in ("image", "media", "multimodal"):
            result.add(key)

    return result


class McpServer:
    """Lightweight MCP server that dispatches JSON-RPC requests to Ralph tools.

    Per-invocation reset contract: a single ``McpServer`` is a per-subprocess
    singleton; it may be reused across multiple agent attempts within the same
    command-line invocation. The soft wrap-up nag (and the hard ceiling it
    warns about) is owned by ONE agent attempt: each attempt boundary MUST
    call :meth:`reset_session_budget` (in-process) or send the wire-level
    ``notifications/reset_wrapup`` JSON-RPC method (over HTTP from
    :class:`RestartAwareMcpBridge`) so the budget is re-armed. See
    ``ralph.mcp.server._session_wrapup`` for the underlying contract and
    ``ralph.pipeline.effect_executor._run_attempt`` for the production
    wire-up at the per-attempt boundary.
    """

    def __init__(
        self,
        session: McpSession,
        workspace: FsWorkspace,
        registry: ToolBridge,
        *,
        expose_mcp_aliases: bool = True,
        wrapup_provider: Callable[[], str | None] | None = None,
        metrics: McpMetrics | None = None,
        mcp_activity_sink: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._registry = registry
        self._client_capabilities: set[str] | None = None
        self._expose_mcp_aliases = expose_mcp_aliases
        # Optional graduated-session nag: returns a wrap-up banner once the
        # invocation passes the soft threshold, else None. Appended to every
        # tool result so the agent winds down before the hard force-cut.
        self._wrapup_provider = wrapup_provider
        # Observability metrics — counters the production transport wires
        # to record post-header failures, terminal frames, and health-probe
        # outcomes. Tests inject a fresh instance to assert observable behavior
        # without the production default. If left as None, the
        # get_default_metrics() singleton is consulted lazily inside
        # handle_request.
        self._metrics = metrics
        # Optional per-server activity sink. When set, ``_handle_tools_call``
        # invokes it once per call (after the tool name is resolved and
        # validated) so the idle watchdog's MCP-tool-channel evidence
        # surface can defer a NO_OUTPUT_DEADLINE fire while the agent is
        # actively using the MCP. The default is None (legacy) for tests
        # that do not exercise the activity channel; production wiring
        # goes through the per-task contextvar registered in
        # ``_activity_sink`` (set_active_sink) so concurrent agent runs
        # do not stomp on each other.
        self._mcp_activity_sink = mcp_activity_sink

    def reset_session_budget(self) -> None:
        """Re-arm the soft wrap-up nag (and the hard ceiling) for a fresh attempt.

        Called by the orchestrator at the top of every ``_run_attempt`` in
        ``ralph.pipeline.effect_executor`` so a retried agent (e.g. after an
        artifact-missing failure) starts with ``elapsed=0`` on the very first
        tool result instead of inheriting the prior attempt's elapsed time.

        The reset creates a fresh :class:`SessionWrapupBudget` backed by the
        production :class:`SystemClock` and the canonical
        ``SESSION_SOFT_WRAPUP_SECONDS`` / ``MAX_SESSION_SECONDS`` defaults
        from :mod:`ralph.timeout_defaults`. The previous budget is replaced
        in-place; the new provider retains the same
        ``Callable[[], str | None]`` signature so no caller signature changes.

        No-op when ``wrapup_provider`` was None at construction time (the
        default; tests that do not exercise the nag have no provider to
        reset). The reset is also reachable over the wire via the
        ``notifications/reset_wrapup`` JSON-RPC method (see
        :meth:`_dispatch_request`).
        """
        if self._wrapup_provider is None:
            return
        budget = SessionWrapupBudget(
            SystemClock(),
            soft_seconds=SESSION_SOFT_WRAPUP_SECONDS,
            hard_seconds=MAX_SESSION_SECONDS,
        )
        self._wrapup_provider = budget.notice

    def handle_request(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        # Uniform transport safety net: no method handler may crash the
        # transport. An unhandled exception in ANY handler (tools/list,
        # initialize, resources/*, or a bug in tools/call) is converted to a
        # JSON-RPC -32603 error so an MCP client always receives a well-formed
        # response instead of a bare HTTP 500 it can only read as a broken or
        # empty session. tools/call keeps its own catch for the common,
        # non-fatal tool-dispatch-error case (a clearer message); this outer
        # net covers everything else.
        try:
            return self._dispatch_request(request, state)
        except Exception as exc:
            logger.error("MCP request handler crashed for method={}: {}", request.method, exc)
            metrics = self._metrics if self._metrics is not None else get_default_metrics()
            metrics.record_post_header_failure(
                request_id=request.msg_id,
                method=request.method,
                session_impl=type(self._session).__name__,
                cause=type(exc).__name__,
            )
            error = {"code": -32603, "message": f"Internal server error: {exc}"}
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                state,
            )

    def _dispatch_request(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        if request.method == "notifications/initialized":
            return (None, ServerState.RUNNING)
        if request.method == "notifications/reset_wrapup":
            # Wire-level seam for the per-attempt reset contract. The
            # orchestrator's RestartAwareMcpBridge posts this method to the
            # inner subprocess over HTTP at the start of every _run_attempt
            # so the soft nag does not carry over from a prior attempt.
            # Fire-and-forget: no payload, no error, no state change.
            self.reset_session_budget()
            return (None, state)
        if request.method == "tools/call":
            return self._handle_tools_call(request, state)

        handlers = {
            "initialize": self._handle_initialize,
            "prompts/list": self._handle_prompts_list,
            "resources/list": self._handle_resources_list,
            "resources/templates/list": self._handle_resource_templates_list,
            "resources/read": self._handle_resources_read,
            "tools/list": self._handle_tools_list,
        }
        handler = handlers.get(request.method)
        if handler is not None:
            return handler(request)

        error = {"code": -32601, "message": f"Method not found: {request.method}"}
        return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

    def _handle_initialize(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
        self._client_capabilities = _extract_client_capabilities(request.params)
        self._registry.set_client_capabilities(self._client_capabilities)

        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {"listChanged": False},
                "prompts": {"listChanged": False},
                "resources": {"subscribe": False, "listChanged": False},
            },
            "serverInfo": {"name": "ralph-mcp", "version": __version__},
        }
        return (
            JsonRpcResponse(jsonrpc="2.0", result=result, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _handle_tools_list(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
        tools: list[dict[str, object]] = []
        seen_names: set[str] = set()
        for definition in self._registry.list_definitions():
            raw_entry: dict[str, object] = {
                "name": definition.name,
                "description": definition.description,
                "inputSchema": definition.input_schema,
            }
            tools.append(raw_entry)
            seen_names.add(definition.name)
            if self._expose_mcp_aliases:
                alias = self._alias_for_tool_name(definition.name)
                if alias and alias != definition.name and alias not in seen_names:
                    tools.append(
                        {
                            "name": alias,
                            "description": definition.description,
                            "inputSchema": definition.input_schema,
                        }
                    )
                    seen_names.add(alias)
        # Runtime invariant: no duplicate names in the tools list. The alias
        # emission rule ensures this by construction (we only emit an alias
        # that differs from the raw name and was not already seen), but we
        # re-check here so a future regression in the alias builder cannot
        # silently break the strict-MCP client contract.
        names = [entry["name"] for entry in tools]
        if len(names) != len(set(names)):
            raise RuntimeError(f"_handle_tools_list emitted duplicate tool names: {names}")
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"tools": tools}, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    @staticmethod
    def _alias_for_tool_name(name: str) -> str | None:
        """Return the canonical `mcp__<server>__<tool>` alias for a tool name.

        Returns None if the name does not correspond to a known
        :class:`RalphToolName` member, or if the alias degenerates to the raw
        name (which is excluded by the import-time invariant in this module
        but we still guard here for safety).
        """
        try:
            member = RalphToolName(name)
        except ValueError:
            return None
        alias = claude_tool_name(member, server_name=RALPH_MCP_SERVER_NAME)
        if alias == name:
            return None
        return alias

    @staticmethod
    def _resolve_alias_to_canonical(name: str) -> str | None:
        """Resolve a possibly-aliased tool name to its registered canonical name.

        If ``name`` matches the `mcp__<server>__<tool>` alias pattern with the
        expected server name, the canonical tool name is returned. Returns
        None for non-aliased names or aliases that do not correspond to a
        known tool — callers fall through to the original name so the standard
        "Tool is not registered" error surfaces with the same message the
        operator sees in live logs.
        """
        prefix = f"mcp__{RALPH_MCP_SERVER_NAME}__"
        if not name.startswith(prefix):
            return None
        raw = name[len(prefix) :]
        try:
            return str(RalphToolName(raw))
        except ValueError:
            return None

    def _handle_prompts_list(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"prompts": []}, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _handle_resources_list(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        resources: list[dict[str, object]] = []
        resources.extend(
            entry.resource_list_entry() for entry in self._session.media_manifest.list_entries()
        )
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"resources": resources}, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _handle_resource_templates_list(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        templates: list[dict[str, object]] = []
        if is_policy_approved(self._session.check_capability("media.read")):
            templates.append(
                {
                    "uriTemplate": "ralph://media/{artifact_id}",
                    "name": "Ralph media artifact",
                    "description": (
                        "Binary media artifact stored by read_media. "
                        "Retrieve via resources/read with the full URI."
                    ),
                }
            )
        return (
            JsonRpcResponse(
                jsonrpc="2.0",
                result={"resourceTemplates": templates},
                msg_id=request.msg_id,
            ),
            ServerState.RUNNING,
        )

    def _handle_resources_read(
        self, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        params = request.params or {}
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri:
            error = {"code": -32602, "message": "resources/read requires a 'uri' parameter"}
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )

        artifact_id = parse_media_uri(uri)
        if artifact_id is None:
            error = {
                "code": -32602,
                "message": (
                    f"Unsupported resource URI: '{uri}'. Expected ralph://media/<artifact_id>"
                ),
            }
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )

        entry = self._session.media_manifest.get(artifact_id)
        if entry is None:
            error = {"code": -32602, "message": f"Resource not found: '{uri}'"}
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )

        raw_bytes = entry.load_bytes()
        if raw_bytes is None:
            error = {"code": -32602, "message": f"Resource bytes no longer available: '{uri}'"}
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )

        blob = _base64.b64encode(raw_bytes).decode("ascii")
        contents: list[dict[str, object]] = [
            {"uri": entry.uri, "mimeType": entry.mime_type, "blob": blob},
        ]
        return (
            JsonRpcResponse(
                jsonrpc="2.0",
                result={"contents": contents},
                msg_id=request.msg_id,
            ),
            ServerState.RUNNING,
        )

    def _handle_tools_call(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        params = request.params or {}
        tool_name = params.get("name")
        if not isinstance(tool_name, str) or not tool_name:
            error = {"code": -32602, "message": "tools/call requires a tool name"}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        arguments_value = params.get("arguments", {})
        if isinstance(arguments_value, str):
            arguments_value = repair_json_containers(arguments_value)
        if not isinstance(arguments_value, dict):
            error = {"code": -32602, "message": "tools/call arguments must be an object"}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        # Resolve the alias `mcp__<server>__<tool>` to its canonical
        # registered tool name BEFORE dispatch. This is what makes the
        # strict-MCP client contract (Claude Code's `mcp__<server>__<tool>`
        # invocations) routable. If the alias does not correspond to any
        # known tool, the original name is used so the dispatch failure
        # surfaces with the live error message the operator sees in logs.
        resolved_name = self._resolve_alias_to_canonical(tool_name)
        if resolved_name is not None:
            tool_name = resolved_name

        # Notify the activity sink BEFORE dispatch so the channel is
        # recorded on the same logical call (success or error) — the
        # watchdog treats both as evidence of demonstrable work. The
        # per-server sink takes precedence when set (tests use this
        # path); the contextvar sink is the production path so concurrent
        # agent runs do not stomp on each other.
        self._invoke_activity_sinks(tool_name)

        try:
            raw_result = self._registry.dispatch(
                tool_name, dict(arguments_value), host_session=self._session
            )
        except (InvalidParamsError, CapabilityDeniedError) as exc:
            raw_result = ToolResult(
                content=[ToolContent.text_content(str(exc))],
                is_error=True,
            )
        except Exception as exc:
            error = {"code": -32603, "message": str(exc)}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        to_dict = cast("_ToDict | None", getattr(raw_result, "to_dict", None))
        payload_source = to_dict() if callable(to_dict) else raw_result
        payload = self._build_tools_call_payload(payload_source)
        self._maybe_append_wrapup_notice(payload)
        return (
            JsonRpcResponse(jsonrpc="2.0", result=payload, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _maybe_append_wrapup_notice(self, payload: dict[str, object]) -> None:
        """Append the graduated-session wrap-up banner to a tool result, if due."""
        if self._wrapup_provider is None:
            return
        notice = self._wrapup_provider()
        if not notice:
            return
        content = payload.get("content")
        block = {"type": "text", "text": notice}
        if isinstance(content, list):
            cast("list[object]", content).append(block)
        else:
            payload["content"] = [block]

    def _invoke_activity_sinks(self, tool_name: str) -> None:
        """Notify the activity sinks of a tools/call invocation.

        Two sinks are consulted:

        1. The per-server ``mcp_activity_sink`` (set in ``__init__``). Used
           by tests that need a sink bound to a specific McpServer
           instance; the canonical example is the test in
           tests/mcp/test_mcp_activity_sink.py that asserts the sink is
           called when a tools/call is processed.
        2. The per-task contextvar sink (set via
           ``_activity_sink.set_active_sink``). This is the production
           path: the per-run watchdog registers itself before its lines
           loop starts and unregisters in a finally block, so concurrent
           agent runs in the same process do not stomp on each other.

        A buggy sink must not crash the JSON-RPC dispatch path, so the
        per-server sink is invoked in a try/except. The contextvar sink
        is already exception-swallowing in ``invoke_active_sink``.
        """
        if self._mcp_activity_sink is not None:
            try:
                self._mcp_activity_sink(tool_name)
            except Exception:
                logger.opt(exception=True).debug(
                    "MCP server: per-server activity sink raised (suppressed)"
                )
        if get_active_sink() is not None:
            invoke_active_sink(tool_name)

    def _build_tools_call_payload(self, payload_source: object) -> dict[str, object]:
        if isinstance(payload_source, dict):
            payload = cast("dict[str, object]", dict(payload_source))
            result_obj = payload.get("result")
            if isinstance(result_obj, dict):
                payload = cast("dict[str, object]", dict(result_obj))
            if "content" not in payload:
                payload["content"] = _serialize_content_blocks(payload_source)
            return payload

        decoded_payload = _decode_json_payload_from_content(payload_source)
        if decoded_payload is not None:
            return decoded_payload
        return {"content": _serialize_content_blocks(payload_source)}
