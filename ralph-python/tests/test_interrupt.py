from __future__ import annotations

import importlib
import signal

from ralph import interrupt as interrupt_module


def test_interrupt_handler_sets_flag() -> None:
    module = importlib.reload(interrupt_module)
    previous_handler = signal.getsignal(signal.SIGINT)

    assert not module.user_interrupted_occurred()

    try:
        module.setup_interrupt_handler()
        signal.raise_signal(signal.SIGINT)
        assert module.user_interrupted_occurred()
    finally:
        signal.signal(signal.SIGINT, previous_handler)
