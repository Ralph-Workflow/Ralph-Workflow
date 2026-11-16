from __future__ import annotations

import sys

print('data: {"type":"text","content":"Gemini running"}', flush=True)
print(
    'data: {"type":"tool_call","name":"Read","args":{"path":"ralph/display/record_writer.py"}}',
    flush=True,
)
print('data: {"type":"tool_result","result":"record source"}', flush=True)
sys.stderr.write("ralph-test-harness-auth-marker: 401 unauthorized\n")
sys.stderr.flush()
sys.exit(1)
