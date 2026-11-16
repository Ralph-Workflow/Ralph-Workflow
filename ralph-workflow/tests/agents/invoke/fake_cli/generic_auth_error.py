from __future__ import annotations

import sys

print('{"type":"text","content":"Generic checks the display.\\n\\n"}', flush=True)
print("[plain] tool: read_file", flush=True)
print('{"type":"error","error":"unauthorized"}', flush=True)
sys.stderr.write("ralph-test-harness-auth-marker: 401 unauthorized\n")
sys.stderr.flush()
sys.exit(1)
