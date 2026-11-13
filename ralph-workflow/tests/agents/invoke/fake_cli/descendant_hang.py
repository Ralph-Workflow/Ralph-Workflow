from __future__ import annotations

import os
import time

if os.fork() == 0:
    os.close(0)
    time.sleep(30)
    os._exit(0)

print("agent parent completed", flush=True)
time.sleep(0.1)
