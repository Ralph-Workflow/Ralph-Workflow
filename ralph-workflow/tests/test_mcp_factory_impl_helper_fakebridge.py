from __future__ import annotations

from tests.test_mcp_factory_impl_helper_fakeprocess import FakeProcess


class FakeBridge:
    def __init__(
        self,
        endpoint: str,
        pid: int,
        run_id: str = "test-run",
        *,
        raise_on_shutdown: BaseException | None = None,
    ) -> None:
        self._endpoint = endpoint
        self._run_id = run_id
        self.process = FakeProcess(pid)
        self.shutdown_calls = 0
        self._raise_on_shutdown = raise_on_shutdown

    @property
    def run_id(self) -> str:
        return self._run_id

    def start(self) -> None:
        return None

    def agent_endpoint_uri(self) -> str:
        return self._endpoint

    def endpoint_uri(self) -> str:
        return self._endpoint

    def shutdown(self) -> None:
        self.shutdown_calls += 1
        if self._raise_on_shutdown is not None:
            raise self._raise_on_shutdown
