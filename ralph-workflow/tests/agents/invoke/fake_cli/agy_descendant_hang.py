from __future__ import annotations

import os
import signal
import sys
import threading

print(
    '{"event":"init","conversation_id":"00000000-0000-0000-0000-000000000002","init":{"model":"gemini-3.6-flash-low","cwd":"/workspace","tools":["read_file","write_to_file"],"permission_mode":"always-proceed"}}',
    flush=True,
)
print(
    '{"event":"step_update","step_update":{"conversation_id":"00000000-0000-0000-0000-000000000002","step_index":0,"state":"ACTIVE","step_type":"tool","tool_name":"read_file","tool_info":{"name":"read_file","parameters":{"TargetFile":"/workspace/hello.txt"}}}}',
    flush=True,
)
print(
    '{"event":"step_update","step_update":{"conversation_id":"00000000-0000-0000-0000-000000000002","step_index":0,"state":"DONE","step_type":"tool","tool_name":"read_file","duration_seconds":0.01,"tool_info":{"name":"read_file","parameters":{"TargetFile":"/workspace/hello.txt"},"output":"hello"}}}',
    flush=True,
)
print(
    '{"event":"result","result":{"conversation_id":"00000000-0000-0000-0000-000000000002","status":"SUCCESS","response":"Done\n","duration_seconds":1.0,"num_turns":1}}',
    flush=True,
)


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
