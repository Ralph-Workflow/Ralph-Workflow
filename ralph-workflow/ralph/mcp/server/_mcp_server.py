"""Lightweight MCP server implementation for the fallback HTTP runtime."""

from __future__ import annotations

import base64 as _base64
import hashlib
import json
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph import __version__
from ralph.agents.system_clock import SystemClock
from ralph.mcp.artifacts.policy_outcomes import is_policy_approved
from ralph.mcp.multimodal.artifacts import infer_modality_and_mime
from ralph.mcp.multimodal.capabilities import inline_image_roundtrip_unsafe
from ralph.mcp.multimodal.resources import parse_media_uri
from ralph.mcp.server._activity_sink import get_active_sink, invoke_active_sink
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._metrics import McpMetrics, get_default_metrics
from ralph.mcp.server._schema_flavor import (
    OPENAI_FUNCTION_FLAVOR,
    flatten_root_schema_for_openai_function,
    schema_flavor_for_client_name,
)
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._session_wrapup import SessionWrapupBudget
from ralph.mcp.server._wire_ledger import append_wire_record
from ralph.mcp.tools._exec_resource_uri import parse_exec_uri
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
    ToolResult,
)
from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME, RalphToolName, claude_tool_name
from ralph.mcp.upstream.client import carries_upstream_media_blocks
from ralph.timeout_defaults import MAX_SESSION_SECONDS, SESSION_SOFT_WRAPUP_SECONDS

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


def _client_name_from_initialize_params(params: dict[str, object] | None) -> str | None:
    """Extract ``clientInfo.name`` from an ``initialize`` request's params.

    Purely structural: an absent params dict, a non-dict ``clientInfo``,
    or a non-string name all return ``None`` so a malformed handshake
    degrades to the default full-JSON-Schema advertisement instead of
    failing the handshake.
    """
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
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        if callable(to_dict):
            serialized.append(to_dict())
            continue

        model_dump = cast(
            "_ModelDump | None", getattr(block, "model_dump", None)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
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
    """Return the JSON payload a tool encoded inside its first text block.

    Public because of what it can do: it REPLACES the whole tool payload
    with JSON decoded out of a text block, after the upstream contract
    has already inspected that block and found nothing to normalise. A
    media block smuggled inside the text would re-enter the payload
    having never been through the contract -- the incident's wire shape,
    by a route that skips the guard. The refusal below is the guard, and
    a guard nothing can reach from outside the module is a guard nothing
    can test.
    """
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
        # REFUSED. This path replaces the whole tool payload with JSON
        # decoded out of a TEXT block, after the upstream contract has
        # already inspected that text block and found nothing to
        # normalise. A media block smuggled inside the text therefore
        # re-entered the payload having never been through the
        # contract, which is how the incident's wire shape reaches an
        # agent. A text block that decodes to media is not a payload
        # this server will serve; the text is still delivered as text.
        return None
    return cast("dict[str, object]", decoded)


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
        cycle_deadline_provider: Callable[[], str | None] | None = None,
        metrics: McpMetrics | None = None,
        mcp_activity_sink: Callable[[str], None] | None = None,
    ) -> None:
        self._session = session
        self._workspace = workspace
        self._registry = registry
        self._expose_mcp_aliases = expose_mcp_aliases
        # Schema flavor negotiated at the MCP ``initialize`` handshake:
        # clients whose backing API cannot consume full JSON Schema at
        # the tool-schema ROOT (``kimi-code`` -> Moonshot's OpenAI-style
        # function parameters) receive a flattened root advertisement.
        # ``None`` (the default, before any handshake and for every other
        # client) keeps the full JSON Schema contract. One McpServer
        # serves exactly one agent client per subprocess, so per-instance
        # state is the correct scope.
        self._schema_flavor: str | None = None
        # Optional graduated-session nag: returns a wrap-up banner once the
        # invocation passes the soft threshold, else None. Appended to every
        # tool result so the agent winds down before the hard force-cut.
        self._wrapup_provider = wrapup_provider
        # Optional cycle-deadline nag: returns the plan-to-final-commit
        # timebox banner once the cycle passes its warning point, else None.
        # Rides on tool results because the prompt appendix that starts an
        # invocation is lost to context compaction and never reaches an
        # invocation that began before the warning point.
        self._cycle_deadline_provider = cycle_deadline_provider
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
            self._append_wire_record_best_effort(request)
            return (None, ServerState.RUNNING)
        if request.method == "notifications/reset_wrapup":
            # Wire-level seam for the per-attempt reset contract. The
            # orchestrator's RestartAwareMcpBridge posts this method to the
            # inner subprocess over HTTP at the start of every _run_attempt
            # so the soft nag does not carry over from a prior attempt.
            # Fire-and-forget: no payload, no error, no state change.
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
            # F2/S-1 (Evidence Provenance): chain every dispatched JSON-RPC
            # request method on the wire ledger, not only tools/call, so the
            # ledger is a fixture generator for a whole session (F5) rather
            # than a single method.
            self._append_wire_record_best_effort(request)
            return handler(request)

        error = {"code": -32601, "message": f"Method not found: {request.method}"}
        return (JsonRpcResponse(jsonrpc="2.0", error=error, msg_id=request.msg_id), state)

    def _append_wire_record_best_effort(self, request: JsonRpcRequest) -> None:
        """Chain ``request`` onto the wire ledger, never raising into dispatch.

        S-2 (Evidence Provenance G2): every dispatched JSON-RPC frame is
        chained, including the two notification methods
        (``notifications/initialized``, ``notifications/reset_wrapup``) that
        are handled before the generic ``handlers`` dict below — without this
        helper those two frames never reached the ledger, unlike every other
        method. ``append_wire_record()`` is a no-op without a broker secret
        (A5), and a test double lacking the full McpSession/FsWorkspace
        surface must not turn a real dispatch into an error — the ledger is
        diagnostic evidence, never load-bearing.
        """
        try:
            append_wire_record(
                self._workspace.root,
                method=request.method,
                tool_name=None,
                params=dict(request.params or {}),
                run_id=self._session.run_id,
                secret=self._session.broker_secret,
            )
        except (AttributeError, OSError, TypeError):
            logger.opt(exception=True).debug(
                "MCP server: wire-ledger append failed (suppressed); "
                "{} dispatch proceeds",
                request.method,
            )

    def _handle_initialize(self, request: JsonRpcRequest) -> tuple[JsonRpcResponse, ServerState]:
        # Negotiate the advertised tool-schema flavor from the client's
        # self-reported ``clientInfo.name``. This is the protocol's own
        # capability-negotiation point: only clients that identify here
        # as needing the OpenAI function flavor (``kimi-code``) get the
        # flattened root schema in ``tools/list``.
        #
        # The flavor is STICKY: once a flavored client has negotiated, a
        # later nameless handshake does NOT reset it. Measured on the
        # wire (kimi-code 0.36.1, 2026-08-17): the CLI reconnects to the
        # long-lived standalone MCP subprocess between turns and Ralph's
        # own preflight/probe handshakes also arrive without a flavor
        # name; a nameless re-handshake that reset the flavor would make
        # the NEXT ``tools/list`` re-advertise the composed roots
        # (``read_file`` et al.) and Moonshot rejects those with the
        # exact 400 this flavor exists to prevent. Sticky-but-never-set
        # stays safe because an unnamed client's tools/list still gets
        # the full JSON Schema advertisement until a flavored handshake
        # arrives.
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
        """Return the ``inputSchema`` to advertise for ``definition``.

        Full JSON Schema by default; the OpenAI-function-flavored root
        (composition keywords dropped, plain-object vocabulary kept) when
        the connecting client negotiated that flavor at ``initialize``.
        The registered ``definition.input_schema`` itself is never
        mutated — only the advertisement changes.
        """
        if self._schema_flavor == OPENAI_FUNCTION_FLAVOR:
            return flatten_root_schema_for_openai_function(definition.input_schema)
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
        # AC-11: include the registered exec spill resources so the
        # agent can see what is replayable through resources/read.
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

        # AC-11: replayable exec resource IDs (returned by the
        # ``format=summary`` exec calls) are resolved before the
        # generic media path. The session must own an exec resource
        # resolver; a missing resolver is reported with the same
        # structured error as an unknown artifact, so legacy
        # clients get a consistent failure mode.
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
        """Return a JSON-RPC error when this caller must not receive the bytes.

        An image withheld from the tool surface has to stay withheld
        here. The delivery guard exists because the caller's agent CLI
        cannot carry image bytes back into its own API request; serving
        them through the resource surface would reopen the same failure
        by a side door and make the tool-side explanation ("cannot accept
        the bytes by any route") untrue.
        """
        # THE PROFILE'S identity, which is what every media tool gates
        # on. These two are not the same value: ``profile_for_caller``
        # adopts a stored profile's transport when the identity itself
        # has none, so a session whose payload carries
        # ``capability_profile.transport = "codex"`` but no serialisable
        # ``model_identity`` -- a shape ``session_payload_json`` emits
        # verbatim, because an unresolvable identity is not written --
        # knew the CLI on the tool surface and did not know it here. The
        # tool then withheld the image and this side door served the
        # bytes, making the tool's own explanation ("cannot accept the
        # bytes by any route") false.
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
        """Resolve a ``ralph://exec/<spill-name>`` URI to its blob.

        AC-11 contract: the session must own an exec resource
        resolver. A missing resolver, an unknown spill, or a
        path-traversal attempt is reported with the same
        structured error as the media path. The blob is truncated
        to the resolver's cap (``MAX_READ_BYTES``) for transport.
        """
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
        """Return the caller-specific delivery and identity facts sealed for a tools/call."""
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

        # F2 (Evidence Provenance): record this tools/call frame on the wire
        # ledger BEFORE dispatch, so the record exists even if the tool
        # handler raises. Provenance.WIRE is granted only by a matching
        # ledger row; append_wire_record() is a no-op (returns None, writes
        # nothing) when the session carries no broker secret — an unsigned
        # server cannot produce a WIRE-grade witness (A5). Best-effort: a
        # session/workspace test double that does not expose the full
        # McpSession/FsWorkspace surface (several existing McpServer tests
        # construct one with only the attributes their scenario needs) must
        # not turn a real tool dispatch into a JSON-RPC error — the ledger is
        # diagnostic evidence, never a load-bearing part of the dispatch path.
        try:
            delivery_mode, provider, model_id, agent_id, capability_profile_digest = self._wire_ledger_facts(
                tool_name, dict(arguments_value)
            )
            append_wire_record(
                self._workspace.root,
                method="tools/call",
                tool_name=tool_name,
                params=dict(arguments_value),
                run_id=self._session.run_id,
                secret=self._session.broker_secret,
                delivery_mode=delivery_mode,
                provider=provider,
                model_id=model_id,
                agent_id=agent_id,
                capability_profile_digest=capability_profile_digest,
            )
        except (AttributeError, OSError, TypeError):
            logger.opt(exception=True).debug(
                "MCP server: wire-ledger append failed (suppressed); tools/call proceeds"
            )

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

        to_dict = cast(
            "_ToDict | None", getattr(raw_result, "to_dict", None)
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        payload_source = to_dict() if callable(to_dict) else raw_result
        payload = self._build_tools_call_payload(payload_source)
        self._maybe_append_notice(payload, self._wrapup_provider)
        self._maybe_append_notice(payload, self._cycle_deadline_provider)
        return (
            JsonRpcResponse(jsonrpc="2.0", result=payload, msg_id=request.msg_id),
            ServerState.RUNNING,
        )

    def _maybe_append_notice(
        self,
        payload: dict[str, object],
        provider: Callable[[], str | None] | None,
    ) -> None:
        """Append one provider's banner as a trailing text block, if it is due."""
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

        decoded_payload = decode_json_payload_from_content(payload_source)
        if decoded_payload is not None:
            return decoded_payload
        return {"content": _serialize_content_blocks(payload_source)}
