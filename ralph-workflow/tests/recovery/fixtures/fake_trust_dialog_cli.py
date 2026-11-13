from __future__ import annotations

import os
import sys
import time
from pathlib import Path

MARKER_FILENAME = "ralph_canary_marker.txt"
MARKER_CONTENT = "RALPH_CANARY_OK"
TRUSTED_ROOT_ENV = "STUB_TRUSTED_ROOT"
BLOCK_ENV = "STUB_BLOCK"
EXIT_DELAY_ENV = "STUB_EXIT_DELAY_SECONDS"

_DIALOG_LINE = "Claude requested permissions to read from /etc/shadow"


def _within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def main() -> int:
    if not os.isatty(0):
        sys.stdout.write("fake-trust-dialog-cli: refusing to run without a TTY\n")
        sys.stdout.flush()
        return 2
    trusted_root = os.environ.get(TRUSTED_ROOT_ENV)
    cwd = Path.cwd()
    if trusted_root and _within(cwd, Path(trusted_root)):
        (cwd / MARKER_FILENAME).write_text(MARKER_CONTENT, encoding="utf-8")
        sys.stdout.write("fake-trust-dialog-cli: trusted cwd, marker written\n")
        sys.stdout.flush()
        return 0
    sys.stdout.write(f"{_DIALOG_LINE}\n")
    sys.stdout.flush()
    if os.environ.get(BLOCK_ENV) == "1":
        while True:
            time.sleep(3600)
    time.sleep(float(os.environ.get(EXIT_DELAY_ENV, "0.5")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
