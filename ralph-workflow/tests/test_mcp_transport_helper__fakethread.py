from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class _FakeThread:
    """Fake threading.Thread-like used by ``tests/test_mcp_transport.py``.

    Records ``join()`` calls so a black-box test can assert that
    ``StdioTransport.close()`` deterministically joins its reader/writer
    threads without inspecting any production private attributes.
    """

    def __init__(self, label: str, on_start: Callable[[], None]) -> None:
        self._label = label
        self._on_start = on_start
        self.join_calls: list[float | None] = []

    def start(self) -> None:
        self._on_start()

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)
