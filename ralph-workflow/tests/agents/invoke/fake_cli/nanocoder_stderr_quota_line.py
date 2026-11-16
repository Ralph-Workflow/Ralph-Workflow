from __future__ import annotations

import sys

print("Nanocoder checks the display.", flush=True)
print("⚒ Executed Read \u00d7 1", flush=True)
print("Nanocoder error: quota exceeded", flush=True)
sys.stderr.write("quota exceeded\n")
sys.stderr.flush()
sys.exit(1)
