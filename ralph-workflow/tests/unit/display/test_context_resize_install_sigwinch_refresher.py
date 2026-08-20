"""Tests for DisplayContext.refreshed() and install_sigwinch_refresher."""

from __future__ import annotations

import signal
import sys
import threading
from unittest.mock import patch

import pytest

from ralph.display.context import DisplayContext, install_sigwinch_refresher, make_display_context


class TestInstallSigwinchRefresher:
    """Tests for install_sigwinch_refresher()."""

    DEFAULT_WIDTH = 80

    def test_noop_on_windows(self) -> None:
        """install_sigwinch_refresher must be a no-op on Windows."""
        with patch.object(sys, "platform", "win32"):
            ctx_holder: list[DisplayContext] = [
                make_display_context(env={"COLUMNS": str(self.DEFAULT_WIDTH)})
            ]
            # Should not raise
            install_sigwinch_refresher(ctx_holder)
            # Context should be unchanged
            assert ctx_holder[0].width == self.DEFAULT_WIDTH

    def test_noop_on_non_main_thread(self) -> None:
        """install_sigwinch_refresher must be a no-op when called from non-main thread."""
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

        # Should not raise, and context unchanged
        assert result[0] is None

    def test_sigwinch_handler_installed_on_posix(self) -> None:
        """On POSIX, install_sigwinch_refresher installs a SIGWINCH handler."""
        if sys.platform == "win32":
            pytest.skip("SIGWINCH not available on Windows")

        ctx_holder: list[DisplayContext] = [make_display_context(env={"COLUMNS": "80"})]

        # Install the handler
        install_sigwinch_refresher(ctx_holder)

        # Verify the handler is installed by checking it would fire
        # We can't easily trigger SIGWINCH in a test, but we can verify
        # that the handler function is registered by checking signal.getsignal
        handler = signal.getsignal(signal.SIGWINCH)
        # The handler should be a callable (not the default SIG_DFL or SIG_IGN)
        assert callable(handler)

    def test_sigwinch_stop_reinstalls_previous_handler(self) -> None:
        """stop() reinstalls the signal handler captured before install_sigwinch_refresher."""
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
        assert any(c[0] == "set" and c[1][0] == signal.SIGWINCH for c in calls if isinstance(c[1], tuple))

        stop()
        assert calls[-1] == ("set", (signal.SIGWINCH, previous_handler))

