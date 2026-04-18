from __future__ import annotations

import asyncio
import inspect
import queue
import threading
import time

import pytest
from rich.console import Console
from rich.live import Live

from ralph.display.parallel_display import ParallelDisplay
from ralph.display.render_thread import RenderThread, UpdateEvent

STOP_DEADLINE_SECONDS = 0.2


def make_live() -> Live:
    console = Console(force_terminal=True, record=True)
    return Live(console=console, auto_refresh=False)


def test_update_event_frozen() -> None:
    event = UpdateEvent(unit_id="u1", kind="output", payload="hello")
    with pytest.raises((AttributeError, TypeError)):
        event.payload = "changed"  # type: ignore[misc]


def test_update_event_kind_output() -> None:
    event = UpdateEvent(unit_id="u1", kind="output", payload="line")
    assert event.kind == "output"
    assert event.unit_id == "u1"
    assert event.payload == "line"


def test_update_event_kind_status() -> None:
    event = UpdateEvent(unit_id="u1", kind="status", payload="RUNNING")
    assert event.kind == "status"


def test_update_event_unit_id_none() -> None:
    event = UpdateEvent(unit_id=None, kind="output", payload="unattributed")
    assert event.unit_id is None


def test_render_thread_is_daemon() -> None:
    q: queue.Queue[UpdateEvent] = queue.Queue()
    live = make_live()
    thread = RenderThread(q=q, renderable_fn=lambda state: "", live=live)
    assert thread.daemon is True


def test_render_thread_is_thread_subclass() -> None:
    q: queue.Queue[UpdateEvent] = queue.Queue()
    live = make_live()
    thread = RenderThread(q=q, renderable_fn=lambda state: "", live=live)
    assert isinstance(thread, threading.Thread)


def test_render_thread_stop_joins_cleanly() -> None:
    q: queue.Queue[UpdateEvent] = queue.Queue()
    live = make_live()
    thread = RenderThread(q=q, renderable_fn=lambda state: "", live=live, refresh_hz=20)

    with live:
        thread.start()
        thread.stop()

    assert not thread.is_alive()


def test_render_thread_stop_interrupts_sleep() -> None:
    q: queue.Queue[UpdateEvent] = queue.Queue()
    live = make_live()
    thread = RenderThread(q=q, renderable_fn=lambda state: "", live=live, refresh_hz=1)

    with live:
        thread.start()
        started_at = time.perf_counter()
        thread.stop()
        elapsed = time.perf_counter() - started_at

    assert elapsed < STOP_DEADLINE_SECONDS


def test_render_thread_drains_queue() -> None:
    q: queue.Queue[UpdateEvent] = queue.Queue()

    for i in range(10):
        q.put(UpdateEvent(unit_id="u1", kind="output", payload=f"line-{i}"))

    live = make_live()
    thread = RenderThread(q=q, renderable_fn=lambda state: "", live=live, refresh_hz=20)

    with live:
        thread.start()
        q.join()
        thread.stop()

    assert q.qsize() == 0


@pytest.mark.asyncio
async def test_concurrent_emitters_drain_queue() -> None:
    q: queue.Queue[UpdateEvent] = queue.Queue()
    live = make_live()
    thread = RenderThread(q=q, renderable_fn=lambda state: "", live=live, refresh_hz=20)
    start = asyncio.Event()

    async def worker(uid: str, lines: list[str]) -> None:
        await start.wait()
        for line in lines:
            q.put(UpdateEvent(unit_id=uid, kind="output", payload=line))

    with live:
        thread.start()
        tasks = [
            asyncio.create_task(worker("u1", ["a", "b", "c"])),
            asyncio.create_task(worker("u2", ["d", "e", "f"])),
            asyncio.create_task(worker("u3", ["g", "h", "i"])),
            asyncio.create_task(worker("u4", ["j", "k", "l"])),
        ]
        start.set()
        await asyncio.gather(*tasks)
        await asyncio.to_thread(q.join)
        thread.stop()

    assert q.qsize() == 0


def test_run_is_not_a_coroutine() -> None:
    q: queue.Queue[UpdateEvent] = queue.Queue()
    live = make_live()
    thread = RenderThread(q=q, renderable_fn=lambda state: "", live=live)
    assert not inspect.iscoroutinefunction(thread.run)


def test_emit_and_set_status_are_not_coroutines() -> None:
    console = Console(force_terminal=False)
    display = ParallelDisplay(console=console, env={})
    assert not inspect.iscoroutinefunction(display.emit)
    assert not inspect.iscoroutinefunction(display.set_status)


def test_render_thread_calls_renderable_fn() -> None:
    q: queue.Queue[UpdateEvent] = queue.Queue()
    received_states: list[dict] = []  # type: ignore[type-arg]

    def renderable_fn(state: dict) -> str:  # type: ignore[type-arg]
        received_states.append(dict(state))
        return ""

    q.put(UpdateEvent(unit_id="u1", kind="output", payload="hello"))
    q.put(UpdateEvent(unit_id="u1", kind="status", payload="RUNNING"))

    live = make_live()
    thread = RenderThread(q=q, renderable_fn=renderable_fn, live=live, refresh_hz=20)

    with live:
        thread.start()
        q.join()
        thread.stop()

    assert len(received_states) > 0


def test_render_thread_forces_live_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    q: queue.Queue[UpdateEvent] = queue.Queue()
    refresh_flags: list[bool] = []

    def renderable_fn(state: dict) -> str:  # type: ignore[type-arg]
        return str(state)

    original_update = Live.update

    def capture_update(
        self: Live,
        renderable: object,
        *,
        refresh: bool = False,
    ) -> None:
        refresh_flags.append(refresh)
        original_update(self, renderable, refresh=refresh)

    monkeypatch.setattr(Live, "update", capture_update)
    q.put(UpdateEvent(unit_id="u1", kind="output", payload="hello"))

    live = make_live()
    thread = RenderThread(q=q, renderable_fn=renderable_fn, live=live, refresh_hz=20)

    with live:
        thread.start()
        q.join()
        thread.stop()

    assert True in refresh_flags
