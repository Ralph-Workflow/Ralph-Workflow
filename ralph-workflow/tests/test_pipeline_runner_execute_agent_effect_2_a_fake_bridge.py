"""_FakeBridge helper for test_pipeline_runner_execute_agent_effect_2_a.py."""

from __future__ import annotations


class _FakeBridge:
    def shutdown(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return "http://127.0.0.1:12345/mcp"
