"""Minimal legacy HTTP+SSE MCP server fixture.

Exposes a legacy `/sse` endpoint that emits an `endpoint` event naming a
message POST endpoint. Client JSON-RPC responses are delivered back over the
open SSE stream as `message` events so tests exercise the full legacy flow.
"""

from __future__ import annotations

import sys
from http.server import ThreadingHTTPServer

from tests.fixtures.fake_sse_mcp_helper__mcphandler import _McpHandler


def main() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    port = server.server_address[1]
    print(port, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
