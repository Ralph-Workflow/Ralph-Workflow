from __future__ import annotations

import os
import signal
from pathlib import Path

_WIRE = Path(__file__).resolve().parents[3] / "display" / "_fixtures" / "opencode_wire.jsonl"
for line in _WIRE.read_text(encoding="utf-8").splitlines():
    if not line.strip():
        continue
    print(line, flush=True)
os.kill(os.getpid(), signal.SIGKILL)
