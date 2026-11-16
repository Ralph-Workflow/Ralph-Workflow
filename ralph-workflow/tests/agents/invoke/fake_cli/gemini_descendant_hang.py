from __future__ import annotations

import os
import signal
import sys
import threading

print('data: {"type":"text","content":"Gemini running"}', flush=True)
print(
    'data: {"type":"tool_call","name":"Read","args":{"path":"ralph/display/record_writer.py"}}',
    flush=True,
)
print('data: {"type":"tool_result","result":"record source"}', flush=True)


def _ignore_sigterm(signum: int, frame: object) -> None:
    del signum, frame


child_pid = os.fork()
if child_pid == 0:
    os.close(0)
    signal.signal(signal.SIGTERM, _ignore_sigterm)
    while True:
        signal.pause()

print("agent parent completed", flush=True)
if "--reap-descendant" in sys.argv:
    os.kill(child_pid, signal.SIGKILL)
    os.waitpid(child_pid, 0)
threading.Event().wait(0.1)
