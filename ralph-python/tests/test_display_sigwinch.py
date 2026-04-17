"""Tests for SIGWINCH handling in ParallelDisplay."""

from __future__ import annotations

import os
import signal

import pytest
from rich.console import Console

from ralph.display.parallel_display import ParallelDisplay, _noop_sigwinch


@pytest.mark.skipif(not hasattr(signal, "SIGWINCH"), reason="SIGWINCH not available on Windows")
def test_sigwinch_handler_installed_in_dashboard_mode() -> None:
    """Verify SIGWINCH handler is installed when display starts in dashboard mode."""
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})  # Should be dashboard mode

    # Store original handler
    original_handler = signal.signal(signal.SIGWINCH, signal.SIG_DFL)

    try:
        pd.start()
        try:
            # After start(), SIGWINCH should be caught by our handler (not cause a crash)
            current_handler = signal.signal(signal.SIGWINCH, signal.SIG_DFL)
            # Our handler should be a callable, not SIG_DFL or SIG_IGN
            assert callable(current_handler), "SIGWINCH handler should be installed"
        finally:
            pd.stop()
    finally:
        # Restore original handler
        signal.signal(signal.SIGWINCH, original_handler)


@pytest.mark.skipif(not hasattr(signal, "SIGWINCH"), reason="SIGWINCH not available on Windows")
def test_sigwinch_does_not_crash_render_thread() -> None:
    """Send multiple SIGWINCH signals rapidly and verify render thread stays alive."""
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})  # Should be dashboard mode

    original_handler = signal.signal(signal.SIGWINCH, signal.SIG_DFL)

    try:
        pd.start()
        try:
            render_thread = pd._render_thread
            assert render_thread is not None
            assert render_thread.is_alive(), "Render thread should be alive before signals"

            for _ in range(5):
                os.kill(os.getpid(), signal.SIGWINCH)

            render_thread = pd._render_thread
            assert render_thread is not None
            assert render_thread.is_alive(), "Render thread should still be alive after SIGWINCH"
        finally:
            if pd._render_thread is not None:
                pd._render_thread._stop_event.set()
    finally:
        signal.signal(signal.SIGWINCH, original_handler)


@pytest.mark.skipif(not hasattr(signal, "SIGWINCH"), reason="SIGWINCH not available on Windows")
def test_sigwinch_handler_restored_after_stop() -> None:
    """Verify original SIGWINCH handler is restored after stop()."""
    console = Console(force_terminal=True, width=120)
    pd = ParallelDisplay(console, {})  # Should be dashboard mode

    # Use a custom handler to verify restoration
    def custom_handler(signum: int, frame: object) -> None:
        pass

    original_handler = signal.signal(signal.SIGWINCH, custom_handler)

    try:
        pd.start()
        pd.stop()

        # After stop(), original handler should be restored
        current_handler = signal.signal(signal.SIGWINCH, signal.SIG_DFL)
        assert current_handler is custom_handler, "Original SIGWINCH handler should be restored"
    finally:
        signal.signal(signal.SIGWINCH, original_handler)


def test_sigwinch_no_op_handler_does_nothing() -> None:
    """Verify the noop handler can be called without raising."""
    _noop_sigwinch(signal.SIGWINCH, None)
    _noop_sigwinch(signal.SIGWINCH, 42)
