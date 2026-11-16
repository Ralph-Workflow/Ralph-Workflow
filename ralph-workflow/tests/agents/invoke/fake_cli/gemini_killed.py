from __future__ import annotations

import os
import signal

print('data: {"type":"text","content":"Gemini running"}', flush=True)
print(
    'data: {"type":"tool_call","name":"Read","args":{"path":"ralph/display/record_writer.py"}}',
    flush=True,
)
print('data: {"type":"tool_result","result":"record source"}', flush=True)
os.kill(os.getpid(), signal.SIGKILL)
