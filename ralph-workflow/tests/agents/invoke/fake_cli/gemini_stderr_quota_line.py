from __future__ import annotations

import sys

print('data: {"type":"text","content":"Gemini running"}', flush=True)
print(
    'data: {"type":"tool_call","name":"Read","args":{"path":"ralph/display/record_writer.py"}}',
    flush=True,
)
print('data: {"type":"tool_result","result":"record source"}', flush=True)
sys.stderr.write("quota exceeded\n")
sys.stderr.flush()
sys.exit(1)
