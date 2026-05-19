from __future__ import annotations

from tests.test_mcp_factory_impl_helper_fakeprocess import FakeProcess


class FakeBridge:
    def __init__(self, endpoint: str, pid: int) -> None:
        self._endpoint = endpoint
        self.process = FakeProcess(pid)
        self.shutdown_calls = 0

    def start(self) -> None:
        return None

    def agent_endpoint_uri(self) -> str:
        return self._endpoint

    def endpoint_uri(self) -> str:
        return self._endpoint

    def shutdown(self) -> None:
        self.shutdown_calls += 1
