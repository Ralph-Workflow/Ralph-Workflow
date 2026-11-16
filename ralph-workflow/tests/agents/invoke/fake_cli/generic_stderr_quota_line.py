from __future__ import annotations

import sys

print('{"type":"text","content":"Generic checks the display.\\n\\n"}', flush=True)
print("[plain] tool: read_file", flush=True)
print('{"type":"text","content":"Generic error: operation failed\\n"}', flush=True)
sys.stderr.write("quota exceeded\n")
sys.stderr.flush()
sys.exit(1)
