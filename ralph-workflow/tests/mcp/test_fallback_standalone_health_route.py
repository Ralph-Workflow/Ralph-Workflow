from __future__ import annotations

import http.client
import threading
from threading import Event

import ralph.mcp.server._fallback_standalone_server as srv_mod


class _FakeMcpServer:
    class _StubSession:
        pass

    _session: object = _StubSession()


def test_health_route_answers_200_on_real_socket() -> None:
    server = srv_mod._FallbackStandaloneServer("127.0.0.1", 0, _FakeMcpServer())
    ready = Event()
    thread = threading.Thread(target=server.run, kwargs={"ready_event": ready}, daemon=True)
    thread.start()
    if not ready.wait(timeout=5.0):
        raise AssertionError("standalone server never signalled readiness")

    host, port = server.bound_address
    conn = http.client.HTTPConnection(host, port, timeout=5)
    try:
        conn.request("GET", "/health")
        resp = conn.getresponse()
        body = resp.read()
        assert resp.status == 200, f"GET /health returned {resp.status}: {body!r}"
        assert b"healthy" in body
    finally:
        conn.close()
        httpd = server._httpd
        if httpd is not None:
            httpd.shutdown_event.set()
            httpd.shutdown()
        thread.join(timeout=5.0)
