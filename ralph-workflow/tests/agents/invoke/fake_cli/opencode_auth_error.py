from __future__ import annotations

import sys

print(
    '{"type":"step_start","timestamp":1785000001000,"sessionID":"s_0001","part":{"id":"p_0001","messageID":"m_0001","sessionID":"s_0001","type":"step-start"}}',
    flush=True,
)
print(
    '{"type":"tool_use","timestamp":1785000002000,"sessionID":"s_0001","part":{"type":"tool","tool":"read","callID":"c_0001","state":{"status":"completed","input":{"filePath":"/workspace/notes.txt"},"output":"hello"},"id":"p_0002","sessionID":"s_0001","messageID":"m_0001"}}',
    flush=True,
)
print(
    '{"type":"step_finish","timestamp":1785000003000,"sessionID":"s_0001","part":{"id":"p_0003","reason":"tool-calls","messageID":"m_0001","sessionID":"s_0001","type":"step-finish"}}',
    flush=True,
)
sys.stderr.write("ralph-test-harness-auth-marker: 401 unauthorized\n")
sys.stderr.flush()
sys.exit(1)
