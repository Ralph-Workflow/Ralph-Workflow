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
