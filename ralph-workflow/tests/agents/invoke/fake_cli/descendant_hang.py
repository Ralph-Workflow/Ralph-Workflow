from __future__ import annotations

import contextlib
import os
import signal
import threading
from pathlib import Path

import pytest

pytestmark = pytest.mark.subprocess_e2e

_KILL_RECORD = Path("descendant_kill.txt")


def _on_sigterm(signum: int, frame: object) -> None:
    del frame
    with contextlib.suppress(Exception):
        _KILL_RECORD.write_text(str(128 + signum))
    os._exit(128 + signum)


child_pid = os.fork()
if child_pid == 0:
    os.close(0)
    signal.signal(signal.SIGTERM, _on_sigterm)
    signal.pause()
    os._exit(0)

print("agent parent completed", flush=True)
threading.Event().wait(0.1)
