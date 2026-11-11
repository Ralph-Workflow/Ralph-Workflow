from __future__ import annotations

import signal
import sys
import threading
from unittest.mock import patch

import pytest

from ralph.display.context import DisplayContext, install_sigwinch_refresher, make_display_context


class TestInstallSigwinchRefresher:
    DEFAULT_WIDTH = 80

    def test_noop_on_windows(self) -> None:
        with patch.object(sys, "platform", "win32"):
            ctx_holder: list[DisplayContext] = [
                make_display_context(env={"COLUMNS": str(self.DEFAULT_WIDTH)})
            ]
            install_sigwinch_refresher(ctx_holder)
            assert ctx_holder[0].width == self.DEFAULT_WIDTH

    def test_noop_on_non_main_thread(self) -> None:
        if sys.platform == "win32":
            pytest.skip("Windows behaves differently with threads and signals")

        ctx_holder: list[DisplayContext] = [make_display_context(env={"COLUMNS": "80"})]
        result: list[Exception | None] = [None]

        def install_from_thread() -> None:
            try:
                install_sigwinch_refresher(ctx_holder)
            except Exception as exc:
                result[0] = exc

        thread = threading.Thread(target=install_from_thread)
        thread.start()
        thread.join()

        assert result[0] is None

    def test_sigwinch_handler_installed_on_posix(self) -> None:
        if sys.platform == "win32":
            pytest.skip("SIGWINCH not available on Windows")

        ctx_holder: list[DisplayContext] = [make_display_context(env={"COLUMNS": "80"})]

        install_sigwinch_refresher(ctx_holder)

        handler = signal.getsignal(signal.SIGWINCH)
        assert callable(handler)

    def test_sigwinch_handler_is_total_non_reentrant_and_restores_default(self) -> None:
        if sys.platform == "win32":
            pytest.skip("SIGWINCH not available on Windows")
        calls: list[object] = []
        installed: dict[int, object] = {}

        def setter(signum: int, handler: object) -> object:
            installed[signum] = handler
            return handler

        ctx_holder: list[DisplayContext] = [make_display_context(env={"COLUMNS": "80"})]

        def on_refresh(_ctx: DisplayContext) -> None:
            calls.append("refresh")
            handler = installed[signal.SIGWINCH]
            assert callable(handler)
            handler(signal.SIGWINCH, None)
            raise RuntimeError("callback failure")

        stop = install_sigwinch_refresher(
            ctx_holder,
            on_refresh,
            signal_getter=lambda _signum: None,
            signal_setter=setter,
        )
        handler = installed[signal.SIGWINCH]
        assert callable(handler)
        handler(signal.SIGWINCH, None)
        assert calls == ["refresh"]
        stop()
        assert installed[signal.SIGWINCH] is signal.SIG_DFL

    def test_sigwinch_stop_reinstalls_previous_handler(self) -> None:
        calls: list[tuple[str, object]] = []
        previous_handler = object()

        def fake_getsignal(signum: int) -> object:
            calls.append(("get", signum))
            return previous_handler

        def fake_signal(signum: int, handler: object) -> object:
            calls.append(("set", (signum, handler)))
            return handler

        ctx_holder: list[DisplayContext] = [make_display_context(env={"COLUMNS": "80"})]
        stop = install_sigwinch_refresher(
            ctx_holder,
            signal_getter=fake_getsignal,
            signal_setter=fake_signal,
        )

        assert ("get", signal.SIGWINCH) in calls
        assert any(
            c[0] == "set" and c[1][0] == signal.SIGWINCH for c in calls if isinstance(c[1], tuple)
        )

        stop()
        assert calls[-1] == ("set", (signal.SIGWINCH, previous_handler))
