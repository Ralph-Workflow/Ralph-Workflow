"""Black-box tests for ``ralph.mcp.server._fallback_standalone_server``.

Pins the wt-024 Step 7 contract: the standalone fallback MCP
server's ``run()`` MUST close the listening socket via
``server_close()`` in a ``finally`` block so the TCP FD is
released on every exit path (normal, exception, early return).
"""

from __future__ import annotations

import threading
from threading import Event
from typing import cast

import pytest

import ralph.mcp.server._fallback_standalone_server as srv_mod
from ralph.mcp.server._fallback_standalone_server import (
    _FallbackStandaloneServer,
)


class _CloseCapturingServer:
    """HTTP server stub recording ``server_close`` calls."""

    def __init__(self, *, raise_on_serve: bool = False) -> None:
        self.close_calls: list[None] = []
        self.serve_calls: list[None] = []
        self.server_address = ("127.0.0.1", 0)
        self.mcp_server: object | None = None
        self.state: object = None
        self.shutdown_event: Event | None = None
        self.shutdown_calls: list[None] = []
        self._raise_on_serve = raise_on_serve

    def serve_forever(self, poll_interval: float = 0.1) -> None:
        del poll_interval
        self.serve_calls.append(None)
        if self._raise_on_serve:
            raise RuntimeError("serve_forever exploded")
        if self.shutdown_event is not None:
            self.shutdown_event.wait(timeout=2.0)

    def shutdown(self) -> None:
        self.shutdown_calls.append(None)
        if self.shutdown_event is not None:
            self.shutdown_event.set()

    def server_close(self) -> None:
        self.close_calls.append(None)


class _FakeMcpServer:
    """Minimal McpServer stub providing ``_session`` for the startup banner."""

    class _StubSession:
        pass

    _session: object = _StubSession()


def _patch_http_server_class(
    monkeypatch: pytest.MonkeyPatch, stub: _CloseCapturingServer
) -> None:
    monkeypatch.setattr(
        srv_mod, "_FallbackHttpServer", lambda *a, **kw: stub
    )


def test_run_closes_server_on_normal_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """Normal exit path: ``serve_forever`` returns, ``server_close`` runs."""
    server_stub = _CloseCapturingServer()
    server_stub.shutdown_event = Event()
    _patch_http_server_class(monkeypatch, server_stub)

    fallback = _FallbackStandaloneServer(
        "127.0.0.1", 0, cast("object", _FakeMcpServer())
    )

    def _trigger_shutdown() -> None:
        threading.Event().wait(timeout=0.05)
        server_stub.shutdown()

    t = threading.Thread(target=_trigger_shutdown)
    t.start()
    fallback.run(ready_event=Event())
    t.join(timeout=2.0)

    assert len(server_stub.close_calls) == 1


def test_run_closes_server_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exception in ``serve_forever`` must still trigger ``server_close``."""
    server_stub = _CloseCapturingServer(raise_on_serve=True)
    _patch_http_server_class(monkeypatch, server_stub)

    fallback = _FallbackStandaloneServer(
        "127.0.0.1", 0, cast("object", _FakeMcpServer())
    )

    with pytest.raises(RuntimeError, match="serve_forever exploded"):
        fallback.run(ready_event=Event())

    assert len(server_stub.close_calls) == 1
