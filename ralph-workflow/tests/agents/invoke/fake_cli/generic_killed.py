from __future__ import annotations

import os
import signal

print('{"type":"text","content":"Generic checks the display.\\n\\n"}', flush=True)
print("[plain] tool: read_file", flush=True)
print('{"type":"error","error":"generic agent execution failed"}', flush=True)
os.kill(os.getpid(), signal.SIGKILL)
