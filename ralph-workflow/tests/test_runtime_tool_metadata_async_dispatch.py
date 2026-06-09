"""The FastMCP tool dispatch must run sync tool handlers off the event loop.

A synchronous MCP tool handler (e.g. ``exec``, which blocks on a subprocess for
up to its timeout) must not run directly on the asyncio event loop. If it does, a
single long-running tool call freezes the entire server — no SSE streaming, no
keepalives, no concurrent requests — which the OpenCode MCP client surfaces as
``-32001 Request timed out``.

This pins the contract that ``call_fn_with_arg_validation`` offloads a sync
handler to a worker thread, leaving the event loop free to make progress.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, cast

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import _all_capability_values, _create_tool, _make_tool_metadata
from ralph.mcp.tools.bridge import ToolDefinition, build_ralph_tool_registry
from ralph.workspace.memory import MemoryWorkspace


async def test_sync_handler_does_not_block_the_event_loop() -> None:
    order: list[object] = []
    release = threading.Event()

    def sync_handler(**_kwargs: object) -> str:
        # Blocks the thread it runs on until released. If this runs ON the event
        # loop, the loop cannot schedule ``releaser`` and ``release`` is never
        # set, so the wait falls through on timeout having seen released=False.
        released = release.wait(timeout=1.0)
        order.append(("handler_returned", released))
        return "result"

    metadata = cast("Any", _make_tool_metadata(required=set(), property_types={}))

    async def releaser() -> None:
        await asyncio.sleep(0)
        order.append("releaser_ran")
        release.set()

    result, _ = await asyncio.gather(
        metadata.call_fn_with_arg_validation(sync_handler, False, {}, None),
        releaser(),
    )

    assert result == "result"
    # The releaser ran (and released the handler) BEFORE the handler returned —
    # only possible if the handler was offloaded off the event loop.
    assert order == ["releaser_ran", ("handler_returned", True)]


async def test_registered_tool_dispatch_runs_off_the_event_loop() -> None:
    # EVERY tool — Ralph-native and proxied third-party/upstream MCP tools — is
    # registered through ``_create_tool``. This pins that a slow tool dispatched
    # that way (here a blocking upstream-style proxy) does not freeze the loop, so
    # the async guarantee is shared by all tools, not special-cased to exec.
    order: list[object] = []
    release = threading.Event()

    class _BlockingUpstreamRegistry:
        def dispatch(self, _name: str, _params: dict[str, object]) -> dict[str, object]:
            released = release.wait(timeout=1.0)
            order.append(("dispatch_returned", released))
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}

    definition = ToolDefinition(
        name="slow_upstream_tool",
        description="",
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    tool = cast("Any", _create_tool(cast("Any", _BlockingUpstreamRegistry()), definition))

    async def releaser() -> None:
        await asyncio.sleep(0)
        order.append("releaser_ran")
        release.set()

    await asyncio.gather(
        tool.fn_metadata.call_fn_with_arg_validation(tool.fn, False, {}, None),
        releaser(),
    )

    assert order == ["releaser_ran", ("dispatch_returned", True)]


def test_every_registered_tool_inherits_the_async_offload() -> None:
    # Drift guard for the single-chokepoint guarantee: every real tool registered
    # through `_create_tool` is marked is_async=False, so it inherits the
    # worker-thread offload. If a tool is ever registered as is_async=True over a
    # synchronous handler, it would run on the event loop and this fails.
    session = AgentSession(
        session_id="offload-test",
        run_id="offload-run",
        drain="standalone",
        capabilities=_all_capability_values(),
    )
    registry = build_ralph_tool_registry(
        session, MemoryWorkspace(), upstream_registry=None, mcp_config=None
    )
    definitions = list(registry.list_definitions())
    assert definitions, "expected the registry to expose tools"
    for definition in definitions:
        tool = cast("Any", _create_tool(registry, definition))
        assert tool.is_async is False, f"tool {definition.name} bypasses the async offload"


async def test_sync_handler_exception_propagates_through_the_offload() -> None:
    # Offloading to a worker thread must not swallow or wrap handler exceptions —
    # they must surface on the awaiting coroutine exactly as a direct call would,
    # so the MCP SDK maps them to the same JSON-RPC error.
    class _BoomError(RuntimeError):
        pass

    def boom(**_kwargs: object) -> object:
        raise _BoomError("kaboom")

    metadata = cast("Any", _make_tool_metadata(required=set(), property_types={}))

    with pytest.raises(_BoomError, match="kaboom"):
        await metadata.call_fn_with_arg_validation(boom, False, {}, None)
