
from __future__ import annotations

import base64 as _base64
import hashlib
import json
import time
from typing import TYPE_CHECKING, Final, cast

from loguru import logger

from ralph import __version__
from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.multimodal.artifacts import infer_modality_and_mime
from ralph.mcp.multimodal.capabilities import inline_image_roundtrip_unsafe
from ralph.mcp.multimodal.resources import parse_media_uri
from ralph.mcp.protocol.cycle_deadline_env import development_warning_is_active
from ralph.mcp.server._activity_relay import ActivityRelayError
from ralph.mcp.server._activity_sink import get_active_sink, invoke_active_sink
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._metrics import McpMetrics, get_default_metrics
from ralph.mcp.server._schema_flavor import (
    FUNCTION_CALLING_SCHEMA_FLAVOR,
    normalize_schema_for_function_calling,
    schema_flavor_for_client_name,
)
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._session_wrapup import (
    development_wrapup_notice,
    session_warning_scope,
)
from ralph.mcp.server._wire_ledger import WireLedgerIOError, append_wire_record
from ralph.mcp.tools._exec_resource_uri import parse_exec_uri
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
    ToolResult,
)
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME, RalphToolName, claude_tool_name
from ralph.mcp.upstream.client import carries_upstream_media_blocks

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Protocol

    from ralph.mcp.protocol.session import McpSession
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.tools._exec_resource_protocol import ExecResourceResolverLike
    from ralph.mcp.tools.bridge import ToolBridge
    from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
    from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:

    class _ToDict(Protocol):
        def __call__(self) -> dict[str, object]: ...

    class _ModelDump(Protocol):
        def __call__(self, **kwargs: bool) -> dict[str, object]: ...


for _member in RalphToolName:
    _alias = claude_tool_name(_member)
    if _alias == str(_member):
        raise RuntimeError(
            f"claude_tool_name({_member!r}) degenerated to its raw name; "
            "alias emission in _handle_tools_list would be a no-op"
        )


def _client_name_from_initialize_params(params: dict[str, object] | None) -> str | None:
    if not isinstance(params, dict):
        return None
    client_info = params.get("clientInfo")
    if not isinstance(client_info, dict):
        return None
    name = client_info.get("name")
    return name if isinstance(name, str) else None


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

        to_dict = cast(
            "_ToDict | None", getattr(block, "to_dict", None)
        )
        if callable(to_dict):
            serialized.append(to_dict())
            continue

        model_dump = cast(
            "_ModelDump | None", getattr(block, "model_dump", None)
        )
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


def decode_json_payload_from_content(content_blocks: object) -> dict[str, object] | None:
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
    if not isinstance(decoded, dict) or "content" not in decoded:
        return None
    if carries_upstream_media_blocks(decoded.get("content")):
        return None
    return cast("dict[str, object]", decoded)


_SLOW_PRE_DISPATCH_S: Final[float] = 5.0


class McpServer:

    def __init__(
        self,
        session: McpSession,
        workspace: FsWorkspace,
        registry: ToolBridge,
        *,
        expose_mcp_aliases: bool = True,
        metrics: McpMetrics | None = None,
        mcp_activity_sink: Callable[[str], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._clock = clock
        self._session = session
        self._workspace = workspace
        self._registry = registry
        self._expose_mcp_aliases = expose_mcp_aliases
        self._schema_flavor: str | None = None
        self._metrics = metrics
        self._mcp_activity_sink = mcp_activity_sink
        self._activity_relay_error: str | None = None

    def reset_session_budget(self) -> None:
        return None

    def handle_request(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
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
        if self._activity_relay_error is not None and request.msg_id is not None:
            return (
                JsonRpcResponse(
                    jsonrpc="2.0",
                    error={"code": -32070, "message": self._activity_relay_error},
                    msg_id=request.msg_id,
                ),
                state,
            )
        if request.method == "notifications/initialized":
            self._append_wire_record_best_effort(request)
            return (None, ServerState.RUNNING)
        if request.method == "notifications/reset_wrapup":
            self._append_wire_record_best_effort(request)
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
            self._append_wire_record_best_effort(request)
            return handler(request)

        error = {"code": -32601, "message": f"Method not found: {request.method}"}
        return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

    def _append_wire_record_best_effort(self, request: JsonRpcRequest) -> None:
        try:
            append_wire_record(
                self._workspace.root,
                method=request.method,
                tool_name=None,
                params=dict(request.params or {}),
                run_id=self._session.run_id,
                secret=self._session.broker_secret,
            )
        except (OSError, WireLedgerIOError) as exc:
            logger.debug(
                "MCP server: wire-ledger append failed (suppressed); {} dispatch proceeds: {}",
                request.method,
                exc,
            )
        except (AttributeError, TypeError):
            logger.opt(exception=True).debug(
                "MCP server: wire-ledger append failed (suppressed); {} dispatch proceeds",
                request.method,
            )

    def _handle_initialize(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
        negotiated = schema_flavor_for_client_name(
            _client_name_from_initialize_params(request.params)
        )
        if negotiated is not None:
            self._schema_flavor = negotiated
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

    def _advertised_input_schema(self, definition: ToolDefinition) -> dict[str, object]:
        if self._schema_flavor == FUNCTION_CALLING_SCHEMA_FLAVOR:
            return normalize_schema_for_function_calling(definition.input_schema)
        return definition.input_schema

    def _handle_tools_list(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
        tools: list[dict[str, object]] = []
        seen_names: set[str] = set()
        for definition in self._registry.list_definitions():
            advertised_schema = self._advertised_input_schema(definition)
            raw_entry: dict[str, object] = {
                "name": definition.name,
                "description": definition.description,
                "inputSchema": advertised_schema,
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
                            "inputSchema": advertised_schema,
                        }
                    )
                    seen_names.add(alias)
        names = [entry["name"] for entry in tools]
        if len(names) != len(set(names)):
            raise RuntimeError(f"_handle_tools_list emitted duplicate tool names: {names}")
        return (
            JsonRpcResponse(jsonrpc="2.0", result={"tools": tools}, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    @staticmethod
    def _alias_for_tool_name(name: str) -> str | None:
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
        resolver: ExecResourceResolverLike | None = getattr(
            self._session, "exec_resource_resolver", None
        )
        if resolver is not None:
            resources.extend(entry.resource_list_entry() for entry in resolver.list_entries())
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
        resolver: ExecResourceResolverLike | None = getattr(
            self._session, "exec_resource_resolver", None
        )
        if resolver is not None:
            templates.append(
                {
                    "uriTemplate": "ralph://exec/{spill_name}",
                    "name": "Ralph exec spill",
                    "description": (
                        "Replayed stdout/stderr spill produced by an "
                        "exec command in format=summary mode. Retrieve "
                        "via resources/read with the full URI."
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

        exec_name = parse_exec_uri(uri)
        if exec_name is not None:
            return self._respond_exec_resource(uri, request)

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

        withheld = self._withheld_media_error(entry.modality, uri)
        raw_bytes = None if withheld is not None else entry.load_bytes()
        if withheld is not None or raw_bytes is None:
            error = withheld or {
                "code": -32602,
                "message": f"Resource bytes no longer available: '{uri}'",
            }
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )

        blob = _base64.b64encode(raw_bytes).decode("ascii")
        contents = [
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

    def _withheld_media_error(self, modality: str, uri: str) -> dict[str, object] | None:
        identity = self._session.caller_capability_profile.identity
        if modality != "image" or not inline_image_roundtrip_unsafe(identity):
            return None
        return {
            "code": -32602,
            "message": (
                f"Resource '{uri}' holds image bytes that cannot be delivered to "
                f"transport '{identity.transport}': its agent CLI cannot carry an "
                "inline image back into the model request. Use read_media with "
                "format='metadata' for size, sha256, and pixel dimensions."
            ),
        }

    def _respond_exec_resource(
        self, uri: str, request: JsonRpcRequest
    ) -> tuple[JsonRpcResponse, ServerState]:
        resolver: ExecResourceResolverLike | None = getattr(
            self._session, "exec_resource_resolver", None
        )
        if resolver is None:
            error = {
                "code": -32602,
                "message": (
                    f"Unsupported resource URI: '{uri}'. Exec spill "
                    "resolver is not attached to this session."
                ),
            }
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )
        result = resolver.read(uri)
        if result is None:
            error = {
                "code": -32602,
                "message": f"Resource not found: '{uri}'",
            }
            return (
                JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id),
                ServerState.RUNNING,
            )
        raw_bytes, mime_type, _total_size = result
        blob = _base64.b64encode(raw_bytes).decode("ascii")
        contents: list[dict[str, object]] = [
            {"uri": uri, "mimeType": mime_type, "blob": blob},
        ]
        return (
            JsonRpcResponse(
                jsonrpc="2.0",
                result={"contents": contents},
                msg_id=request.msg_id,
            ),
            ServerState.RUNNING,
        )

    def _wire_ledger_facts(
        self,
        tool_name: str,
        arguments: dict[str, object],
    ) -> tuple[str, str, str | None, str, str]:
        identity = self._session.caller_model_identity
        profile = self._session.caller_capability_profile
        agent_id = self._session.caller_agent_id
        delivery_mode = "not_applicable"
        modality: str | None = None
        if tool_name.endswith("read_image") or tool_name.endswith("media_capture"):
            modality = "image"
        elif tool_name.endswith("read_media"):
            path = arguments.get("path")
            if isinstance(path, str):
                inferred = infer_modality_and_mime(path.rsplit(".", 1)[-1].join((".", "")))
                if inferred is not None:
                    modality = inferred[0]
        if modality is not None:
            delivery_mode = profile.verdict_for(modality).delivery.value
        profile_digest = hashlib.sha256(
            json.dumps(profile.to_payload(), sort_keys=True).encode()
        ).hexdigest()
        return delivery_mode, identity.provider, identity.model_id, agent_id, profile_digest

    def _record_wire_ledger_before_dispatch(
        self, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object] | None:
        try:
            delivery_mode, provider, model_id, agent_id, capability_profile_digest = (
                self._wire_ledger_facts(tool_name, arguments)
            )
            append_wire_record(
                self._workspace.root,
                method="tools/call",
                tool_name=tool_name,
                params=arguments,
                run_id=self._session.run_id,
                secret=self._session.broker_secret,
                delivery_mode=delivery_mode,
                provider=provider,
                model_id=model_id,
                agent_id=agent_id,
                capability_profile_digest=capability_profile_digest,
            )
        except WireLedgerIOError as exc:
            return {"code": -32603, "message": str(exc)}
        except (AttributeError, TypeError):
            logger.opt(exception=True).debug(
                "MCP server: wire-ledger append failed (suppressed); tools/call proceeds"
            )
        return None

    def _handle_tools_call(
        self, request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse, ServerState]:
        params = request.params or {}
        tool_name = params.get("name")
        if not isinstance(tool_name, str) or not tool_name:
            error = {"code": -32602, "message": "tools/call requires a tool name"}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        arguments_value = params.get("arguments", {})
        if not isinstance(arguments_value, dict):
            error = {"code": -32602, "message": "tools/call arguments must be an object"}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        resolved_name = self._resolve_alias_to_canonical(tool_name)
        if resolved_name is not None:
            tool_name = resolved_name

        pre_dispatch_started = self._clock()
        try:
            self._invoke_activity_sinks(tool_name)
        except ActivityRelayError as exc:
            error = {"code": -32070, "message": str(exc)}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        ledger_err = self._record_wire_ledger_before_dispatch(tool_name, dict(arguments_value))
        pre_dispatch_s = self._clock() - pre_dispatch_started
        if pre_dispatch_s >= _SLOW_PRE_DISPATCH_S:
            logger.warning(
                "MCP server: tools/call {} spent {:.1f}s in pre-dispatch (activity relay + "
                "wire ledger) before its handler ran",
                tool_name,
                pre_dispatch_s,
            )
        if ledger_err is not None:
            return (
                JsonRpcResponse(jsonrpc="2.0", error=ledger_err, msg_id=request.msg_id),
                state,
            )

        try:
            warning_active = development_warning_is_active(now_epoch=time.time())
            with session_warning_scope(not warning_active):
                raw_result = self._registry.dispatch(
                    tool_name,
                    dict(arguments_value),
                    host_session=self._session,
                    workspace=self._workspace,
                )
        except (InvalidParamsError, CapabilityDeniedError) as exc:
            raw_result = ToolResult(
                content=[ToolContent.text_content(str(exc))],
                is_error=True,
            )
        except Exception as exc:
            error = {"code": -32603, "message": str(exc)}
            return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

        to_dict = cast(
            "_ToDict | None", getattr(raw_result, "to_dict", None)
        )
        payload_source = to_dict() if callable(to_dict) else raw_result
        payload = self._build_tools_call_payload(payload_source)
        self._maybe_append_notice(
            payload,
            development_wrapup_notice
            if development_warning_is_active(now_epoch=time.time())
            else None,
        )
        return (
            JsonRpcResponse(jsonrpc="2.0", result=payload, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _maybe_append_notice(
        self,
        payload: dict[str, object],
        provider: Callable[[], str | None] | None,
    ) -> None:
        if provider is None:
            return
        notice = provider()
        if not notice:
            return
        content = payload.get("content")
        block = {"type": "text", "text": notice}
        if isinstance(content, list):
            cast("list[object]", content).append(block)
        else:
            payload["content"] = [block]

    def _invoke_activity_sinks(self, tool_name: str) -> None:
        if self._mcp_activity_sink is not None:
            try:
                self._mcp_activity_sink(tool_name)
            except ActivityRelayError as exc:
                self._activity_relay_error = str(exc)
                raise
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

        decoded_payload = decode_json_payload_from_content(payload_source)
        if decoded_payload is not None:
            return decoded_payload
        return {"content": _serialize_content_blocks(payload_source)}
