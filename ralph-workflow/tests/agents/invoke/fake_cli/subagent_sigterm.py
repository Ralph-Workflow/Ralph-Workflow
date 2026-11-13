from __future__ import annotations

import json
import os
import signal

print(json.dumps({"type": "tool_use", "name": "task", "status": "running"}), flush=True)
os.kill(os.getpid(), signal.SIGTERM)
