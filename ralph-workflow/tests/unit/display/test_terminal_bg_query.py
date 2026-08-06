"""Direct unit tests for the OSC 11 terminal-background probe.

Every existing test elsewhere in the suite exercises this module only
indirectly, by mocking ``query_terminal_background_hex`` at the call
boundary (see ``tests/unit/display/test_display_context.py``). PLAN.md's
S-4 closes that gap: ``parse_osc11_reply`` is pure and tested directly
across every supported reply shape, and ``_probe``'s raw-mode-restore-in-
``finally`` contract (B-2) is proven by injecting fakes for
``termios``/``tty``/``os.write``/``os.read`` rather than touching a real
tty or subprocess -- per the test-policy audit's ban on real I/O in
non-``subprocess_e2e`` tests.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator

import pytest

import ralph.display._terminal_bg_query as _mod
from ralph.display._terminal_bg_query import (
    parse_osc11_reply,
    query_terminal_background_hex,
    reset_cache,
)


@pytest.fixture(autouse=True)
def _reset_process_lifetime_cache() -> Iterator[None]:
    """The probe's cache is a module-level global (B-5's documented
    process-lifetime memo) -- reset it before and after every test so
    tests in this file never observe each other's cached result."""
    reset_cache()
    yield
    reset_cache()


# --- parse_osc11_reply: pure function, every supported channel width ---


@pytest.mark.parametrize(
    ("reply", "expected"),
    (
        # 1-hex-digit channel width: a single digit is replicated to a
        # full byte ('f' -> 'ff' -> 255), BEL-terminated.
        ("\x1b]11;rgb:f/0/a\x07", "#FF00AA"),
        # 2-hex-digit channel width (the common case), BEL-terminated.
        ("\x1b]11;rgb:2d/2a/2e\x07", "#2D2A2E"),
        # 3-hex-digit channel width: only the most-significant byte survives.
        ("\x1b]11;rgb:abc/123/456\x07", "#AB1245"),
        # 4-hex-digit channel width (seen in the wild per the module's own
        # docstring: 'fdfd/f6f6/e3e3'), ST-terminated (ESC \\) rather than BEL.
        ("\x1b]11;rgb:2d2d/2a2a/2e2e\x1b\\", "#2D2A2E"),
        # Surrounding OSC framing and any leading garbage must be ignored --
        # only the rgb: body matters.
        ("garbage\x1b]11;rgb:ff/ff/ff\x07trailing", "#FFFFFF"),
    ),
)
def test_parse_osc11_reply_scales_every_supported_channel_width(reply: str, expected: str) -> None:
    assert parse_osc11_reply(reply) == expected


@pytest.mark.parametrize(
    "reply",
    (
        "",
        "\x1b]11;no colour here\x07",
        "\x1b]11;rgb:zz/zz/zz\x07",
        "just some unrelated terminal noise",
        "\x1b]11;rgb:2d/2a\x07",  # only two channels
    ),
)
def test_parse_osc11_reply_returns_none_for_malformed_or_absent_bodies(reply: str) -> None:
    assert parse_osc11_reply(reply) is None


# --- _probe: raw-mode-restore-in-finally contract (B-2), via injected fakes ---


class _FakeTermios:
    """Records every tcgetattr/tcsetattr call instead of touching a real tty."""

    TCSANOW = 0
    TCSADRAIN = 1

    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []
        self.original_attrs: list[str] = ["orig-attrs-sentinel"]

    def tcgetattr(self, fd: int) -> list[str]:
        self.calls.append(("tcgetattr", fd))
        return self.original_attrs

    def tcsetattr(self, fd: int, when: int, attrs: object) -> None:
        self.calls.append(("tcsetattr", fd, when, attrs))


class _RaisingTcgetattrTermios:
    """A fake whose tcgetattr always fails, simulating a backgrounded process."""

    TCSANOW = 0
    TCSADRAIN = 1

    def tcgetattr(self, fd: int) -> list[str]:
        raise OSError("not a terminal")

    def tcsetattr(self, fd: int, when: int, attrs: object) -> None:
        raise AssertionError("tcsetattr must not run: tcgetattr already failed")


class _FakeTty:
    """Records every setraw call instead of touching a real tty."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def setraw(self, fd: int, when: int) -> None:
        self.calls.append((fd, when))


def test_probe_short_circuits_when_no_tty_is_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """B-2: when ``_tty_fd`` finds nothing usable, ``_probe`` must return
    without ever importing/touching ``termios`` -- the non-tty short-circuit
    path the plan names explicitly."""
    monkeypatch.setattr(_mod, "_tty_fd", lambda: None)

    attempted, result = _mod._probe(0.05)

    assert attempted is False
    assert result is None


def test_probe_restores_terminal_mode_in_finally_even_when_the_exchange_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-2: an exception mid-exchange (here, ``os.write`` failing) must still
    reach the ``finally`` clause and restore the original terminal
    attributes -- the probe must never leak raw mode."""
    fake_termios = _FakeTermios()
    fake_tty = _FakeTty()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    monkeypatch.setattr(_mod, "_tty_fd", lambda: (17, False))

    def _raising_write(fd: int, data: bytes) -> int:
        raise OSError("broken pipe")

    monkeypatch.setattr(os, "write", _raising_write)

    attempted, result = _mod._probe(0.05)

    assert attempted is True
    assert result is None
    assert fake_tty.calls == [(17, fake_termios.TCSANOW)]
    assert ("tcsetattr", 17, fake_termios.TCSADRAIN, fake_termios.original_attrs) in fake_termios.calls


def test_probe_completes_a_full_exchange_and_still_restores_terminal_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-2 happy path: a full, successful OSC 11 round trip parses the reply
    AND restores the original terminal attributes -- the restore is
    unconditional, not just an error-path behaviour."""
    fake_termios = _FakeTermios()
    fake_tty = _FakeTty()
    monkeypatch.setitem(sys.modules, "termios", fake_termios)
    monkeypatch.setitem(sys.modules, "tty", fake_tty)
    monkeypatch.setattr(_mod, "_tty_fd", lambda: (17, False))

    written: list[tuple[int, bytes]] = []

    def _record_write(fd: int, data: bytes) -> int:
        written.append((fd, data))
        return len(data)

    def _always_readable(fd: int, timeout: float) -> bool:
        return True

    reply_chunks = iter([b"\x1b]11;rgb:2d/2a/2e\x07"])

    def _read_reply_chunk(fd: int, count: int) -> bytes:
        return next(reply_chunks, b"")

    monkeypatch.setattr(os, "write", _record_write)
    monkeypatch.setattr(_mod, "_wait_readable", _always_readable)
    monkeypatch.setattr(os, "read", _read_reply_chunk)

    attempted, result = _mod._probe(0.05)

    assert attempted is True
    assert result == "#2D2A2E"
    assert written and written[0][0] == 17
    assert ("tcsetattr", 17, fake_termios.TCSADRAIN, fake_termios.original_attrs) in fake_termios.calls


def test_probe_closes_an_owned_fd_and_never_enters_raw_mode_when_tcgetattr_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-2: a backgrounded/non-real tty (``tcgetattr`` raises) must degrade to
    ``None`` without ever calling ``tty.setraw``/``tcsetattr``, and must close
    an fd this call itself opened (``close_fd=True``, e.g. the ``/dev/tty``
    fallback)."""
    monkeypatch.setitem(sys.modules, "termios", _RaisingTcgetattrTermios())
    monkeypatch.setitem(sys.modules, "tty", _FakeTty())
    monkeypatch.setattr(_mod, "_tty_fd", lambda: (23, True))
    closed: list[int] = []
    monkeypatch.setattr(os, "close", closed.append)

    attempted, result = _mod._probe(0.05)

    assert attempted is False
    assert result is None
    assert closed == [23]


# --- query_terminal_background_hex: process-lifetime cache (B-5) ---


def test_query_terminal_background_hex_probes_once_and_caches_for_process_lifetime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[float] = []

    def _fake_probe(timeout: float) -> tuple[bool, str | None]:
        calls.append(timeout)
        return True, "#2D2A2E"

    monkeypatch.setattr(_mod, "_probe", _fake_probe)

    first = query_terminal_background_hex()
    second = query_terminal_background_hex()

    assert first == second == "#2D2A2E"
    assert len(calls) == 1


def test_query_terminal_background_hex_does_not_cache_an_unattempted_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-2: ``attempted=False`` (no tty was ever usable) must not poison the
    cache with a false negative -- every call re-probes."""
    calls: list[float] = []

    def _fake_probe(timeout: float) -> tuple[bool, str | None]:
        calls.append(timeout)
        return False, None

    monkeypatch.setattr(_mod, "_probe", _fake_probe)

    first = query_terminal_background_hex()
    second = query_terminal_background_hex()

    assert first is None
    assert second is None
    assert len(calls) == 2
