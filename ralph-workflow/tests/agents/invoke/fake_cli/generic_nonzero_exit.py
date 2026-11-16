from __future__ import annotations

import sys

print('{"type":"text","content":"Generic checks the display.\\n\\n"}', flush=True)
print("[plain] tool: read_file", flush=True)
print('{"type":"error","error":"generic agent execution failed"}', flush=True)
sys.exit(1)
