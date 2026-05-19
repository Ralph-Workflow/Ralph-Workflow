from __future__ import annotations


class _FakeMcpBridge:
    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"

    def shutdown(self) -> None:
        pass
