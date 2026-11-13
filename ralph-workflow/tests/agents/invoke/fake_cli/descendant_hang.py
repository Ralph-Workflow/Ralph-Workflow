from __future__ import annotations

import os
import signal
import threading

import pytest

pytestmark = pytest.mark.subprocess_e2e

if os.fork() == 0:
    os.close(0)
    signal.pause()
    os._exit(0)

print("agent parent completed", flush=True)
threading.Event().wait(0.1)
