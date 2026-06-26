from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class _FakeThread:
    """Fake threading.Thread-like used by ``tests/test_mcp_transport.py``.

    Records ``join()`` calls so a black-box test can assert that
    ``StdioTransport.close()`` deterministically joins its reader/writer
    threads without inspecting any production private attributes.

    Records the ``daemon`` argument so tests can assert that
    ``StdioTransport.start()`` requests daemon threads for both
    the reader and the writer — this is the precondition the
    ``# resource-lifecycle-ok: bounded-daemon factory`` marker
    in ``ralph/mcp/protocol/transport.py`` relies on. If the
    production code ever stops requesting daemon=True, this
    fake-thread double captures the regression before the
    audit is run.

    ``alive_after_join`` lets a test simulate a wedged reader/writer:
    ``StdioTransport.close()`` MUST observe ``is_alive()`` is True after
    the bounded ``join()`` and log a warning without raising. Default
    is False (cleanly exited) so existing join-coverage tests keep
    working unchanged.
    """

    def __init__(
        self,
        label: str,
        on_start: Callable[[], None],
        alive_after_join: bool = False,
        *,
        daemon: bool | None = None,
    ) -> None:
        self._label = label
        self._on_start = on_start
        self._alive_after_join = alive_after_join
        self.daemon_arg: bool | None = daemon
        self.join_calls: list[float | None] = []
        self.is_alive_calls: int = 0

    def start(self) -> None:
        self._on_start()

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)

    def is_alive(self) -> bool:
        self.is_alive_calls += 1
        return self._alive_after_join
