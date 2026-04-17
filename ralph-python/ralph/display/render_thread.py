from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

    from rich.console import RenderableType
    from rich.live import Live


@dataclass(frozen=True)
class UpdateEvent:
    unit_id: str | None
    kind: Literal["output", "status"]
    payload: str


class RenderThread(threading.Thread):
    def __init__(
        self,
        q: queue.Queue[UpdateEvent],
        renderable_fn: Callable[[dict[str, list[str] | str]], RenderableType],
        live: Live,
        refresh_hz: int = 4,
    ) -> None:
        super().__init__(daemon=True)
        self._queue = q
        self._renderable_fn = renderable_fn
        self._live = live
        self._stop_event = threading.Event()
        self._state: dict[str, list[str] | str] = {}
        self._refresh_hz = refresh_hz

    def _apply(self, event: UpdateEvent) -> None:
        key = event.unit_id or "__unattributed__"
        if event.kind == "output":
            existing = self._state.get(key)
            lines: list[str] = existing if isinstance(existing, list) else []
            self._state[key] = [*lines, event.payload]
        elif event.kind == "status":
            self._state[f"{key}__status__"] = event.payload

    def run(self) -> None:
        while not self._stop_event.is_set():
            while True:
                try:
                    event = self._queue.get_nowait()
                    self._apply(event)
                except queue.Empty:
                    break
            self._live.update(self._renderable_fn(self._state))
            time.sleep(1 / self._refresh_hz)

    def stop(self) -> None:
        self._stop_event.set()
        self.join(timeout=2.0)


__all__ = ["RenderThread", "UpdateEvent"]
