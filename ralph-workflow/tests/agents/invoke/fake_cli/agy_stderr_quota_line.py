from __future__ import annotations

import sys

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
    '{"event":"result","result":{"conversation_id":"00000000-0000-0000-0000-000000000002","status":"ERROR","error":"quota exceeded"}}',
    flush=True,
)
sys.stderr.write("quota exceeded\n")
sys.stderr.flush()
sys.exit(1)
