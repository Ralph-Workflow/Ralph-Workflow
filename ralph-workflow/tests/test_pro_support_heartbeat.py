"""Black-box unit tests for :mod:`ralph.pro_support.heartbeat`."""

from __future__ import annotations

import threading

import pytest

from ralph.pro_support.heartbeat import ProHeartbeatClient


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeClient:
    """Fake httpx-like client that records posts and returns a configurable status."""

    def __init__(
        self,
        status_code: int = 200,
        raise_on_post: BaseException | None = None,
        tick_signal: threading.Event | None = None,
    ) -> None:
        self.posts: list[dict[str, object]] = []
        self._status_code = status_code
        self._raise_on_post = raise_on_post
        self._tick_signal = tick_signal

    def post(
        self,
        url: str,
        *,
        json: dict[str, object] | None = None,
        timeout: float | None = None,
    ) -> _FakeResponse:
        self.posts.append({"url": url, "json": json, "timeout": timeout})
        if self._tick_signal is not None:
            self._tick_signal.set()
        if self._raise_on_post is not None:
            raise self._raise_on_post
        return _FakeResponse(self._status_code)

    def close(self) -> None:
        pass


class _Clock:
    """Controllable monotonic clock for the heartbeat worker."""

    def __init__(self) -> None:
        self._now = 0.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, delta: float) -> None:
        with self._lock:
            self._now += delta


def _make_client(
    *,
    interval: float = 0.05,
    timeout: float = 0.5,
    fake: _FakeClient | None = None,
    clock: _Clock | None = None,
) -> tuple[ProHeartbeatClient, _FakeClient, _Clock]:
    fake = fake if fake is not None else _FakeClient()
    clock = clock if clock is not None else _Clock()
    client = ProHeartbeatClient(
        run_id="run-1",
        token="token-1",
        base_url="http://localhost:7432",
        pid=12345,
        interval_seconds=interval,
        timeout_seconds=timeout,
        httpx_client_factory=lambda: fake,
        clock=clock,
    )
    return client, fake, clock


def test_heartbeat_posts_expected_payload() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(tick_signal=tick_signal)
    client, _, clock = _make_client(fake=fake, interval=0.001)
    client.start()
    try:
        for _ in range(3):
            tick_signal.clear()
            clock.advance(0.001)
            assert tick_signal.wait(timeout=2.0), "heartbeat worker missed a tick"
    finally:
        client.stop()

    assert fake.posts, "expected at least one POST"
    first = fake.posts[0]
    assert first["url"] == "http://localhost:7432/api/heartbeat"
    assert first["timeout"] == 0.5
    payload = first["json"]
    assert isinstance(payload, dict)
    assert payload["run_id"] == "run-1"
    assert payload["token"] == "token-1"
    assert payload["status"] == "running"
    assert payload["pid"] == 12345


def test_heartbeat_401_is_hard_stop() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(status_code=401, tick_signal=tick_signal)
    clock = _Clock()
    client = ProHeartbeatClient(
        run_id="r",
        token="t",
        base_url="http://localhost:7432",
        pid=1,
        interval_seconds=0.05,
        timeout_seconds=0.1,
        httpx_client_factory=lambda: fake,
        clock=clock,
    )
    client.start()
    try:
        # Wait for the first tick (which receives 401). The 401 must
        # hard-stop the worker, so after the first tick the loop must
        # not post again even if the clock advances.
        assert tick_signal.wait(timeout=2.0), "expected the first 401 tick"
        posts_after_first = len(fake.posts)
        assert posts_after_first == 1
        # Clear the signal and advance the clock several intervals.
        tick_signal.clear()
        for _ in range(5):
            clock.advance(0.05)
        # Give the daemon a chance to potentially re-post, bounded.
        end_ticks = tick_signal.wait(timeout=0.05)
        assert not end_ticks, "client must not POST after 401"
        assert len(fake.posts) == 1
    finally:
        client.stop()


def test_heartbeat_404_is_hard_stop() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(status_code=404, tick_signal=tick_signal)
    clock = _Clock()
    client = ProHeartbeatClient(
        run_id="r",
        token="t",
        base_url="http://localhost:7432",
        pid=1,
        interval_seconds=0.05,
        timeout_seconds=0.1,
        httpx_client_factory=lambda: fake,
        clock=clock,
    )
    client.start()
    try:
        # Wait for the first tick (which receives 404). The 404 must
        # hard-stop the worker, so after the first tick the loop must
        # not post again even if the clock advances.
        assert tick_signal.wait(timeout=2.0), "expected the first 404 tick"
        posts_after_first = len(fake.posts)
        assert posts_after_first == 1
        tick_signal.clear()
        for _ in range(5):
            clock.advance(0.05)
        end_ticks = tick_signal.wait(timeout=0.05)
        assert not end_ticks, "client must not POST after 404"
        assert len(fake.posts) == 1
    finally:
        client.stop()


def test_heartbeat_transient_errors_continue_looping() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(status_code=503, tick_signal=tick_signal)
    clock = _Clock()
    client = ProHeartbeatClient(
        run_id="r",
        token="t",
        base_url="http://localhost:7432",
        pid=1,
        interval_seconds=0.001,
        timeout_seconds=0.1,
        httpx_client_factory=lambda: fake,
        clock=clock,
    )
    client.start()
    try:
        # Drive several ticks; each posts then continues.
        for _ in range(3):
            tick_signal.clear()
            clock.advance(0.001)
            assert tick_signal.wait(timeout=2.0), "heartbeat worker missed a tick"
    finally:
        client.stop()
    assert len(fake.posts) >= 2, "transient errors must not stop the loop"


def test_heartbeat_network_exception_is_transient() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(raise_on_post=ConnectionError("simulated"), tick_signal=tick_signal)
    clock = _Clock()
    client = ProHeartbeatClient(
        run_id="r",
        token="t",
        base_url="http://localhost:7432",
        pid=1,
        interval_seconds=0.001,
        timeout_seconds=0.1,
        httpx_client_factory=lambda: fake,
        clock=clock,
    )
    client.start()
    try:
        for _ in range(3):
            tick_signal.clear()
            clock.advance(0.001)
            assert tick_signal.wait(timeout=2.0), "heartbeat worker missed a tick"
    finally:
        client.stop()
    assert len(fake.posts) >= 2


def test_stop_is_idempotent_and_does_not_block() -> None:
    client, _fake, _clock = _make_client(interval=0.001)
    client.start()
    # Yield to the daemon briefly so it enters its first sleep.
    # The daemon is daemonic so we cannot join it; we just assert that
    # many stop() calls in a row return promptly and do not raise.
    for _ in range(10):
        client.stop()
    # Calling stop after the thread has been signalled must remain a no-op.
    client.stop()


def test_start_is_idempotent() -> None:
    client, _fake, _clock = _make_client(interval=0.001)
    client.start()
    t1 = client._thread
    client.start()
    assert client._thread is t1
    client.stop()


def test_construction_rejects_non_positive_intervals() -> None:
    with pytest.raises(ValueError):
        ProHeartbeatClient(
            run_id="r",
            token="t",
            base_url="http://localhost",
            pid=1,
            interval_seconds=0.0,
        )
    with pytest.raises(ValueError):
        ProHeartbeatClient(
            run_id="r",
            token="t",
            base_url="http://localhost",
            pid=1,
            interval_seconds=-1.0,
        )
    with pytest.raises(ValueError):
        ProHeartbeatClient(
            run_id="r",
            token="t",
            base_url="http://localhost",
            pid=1,
            timeout_seconds=0.0,
        )


def test_heartbeat_post_carries_metadata() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(tick_signal=tick_signal)
    client, _, clock = _make_client(fake=fake, interval=0.001)
    client._metadata = {"agent": "claude"}
    client.start()
    try:
        tick_signal.clear()
        clock.advance(0.001)
        assert tick_signal.wait(timeout=2.0), "heartbeat worker missed a tick"
    finally:
        client.stop()
    assert fake.posts
    first_payload = fake.posts[0]["json"]
    assert isinstance(first_payload, dict)
    assert first_payload["metadata"] == {"agent": "claude"}


def test_heartbeat_base_url_strips_trailing_slash() -> None:
    tick_signal = threading.Event()
    fake = _FakeClient(tick_signal=tick_signal)
    clock = _Clock()
    client = ProHeartbeatClient(
        run_id="r",
        token="t",
        base_url="http://localhost:7432/",
        pid=1,
        interval_seconds=0.001,
        timeout_seconds=0.1,
        httpx_client_factory=lambda: fake,
        clock=clock,
    )
    client.start()
    try:
        tick_signal.clear()
        clock.advance(0.001)
        assert tick_signal.wait(timeout=2.0), "heartbeat worker missed a tick"
    finally:
        client.stop()
    assert fake.posts
    assert fake.posts[0]["url"] == "http://localhost:7432/api/heartbeat"
