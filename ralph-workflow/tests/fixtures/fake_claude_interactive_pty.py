from __future__ import annotations

import signal
import sys
import time
from pathlib import Path


def _write_prompt_echo(prompt_path: str) -> None:
    prompt = Path(prompt_path)
    if prompt.exists():
        text = prompt.read_text(encoding="utf-8").strip()
        sys.stdout.write(f"[claude]: prompt={text}\n")
        sys.stdout.flush()


def _handle_sigterm(signum: int, frame: object | None) -> None:
    del signum, frame
    sys.stdout.write("[claude]: received SIGTERM\n")
    sys.stdout.flush()
    raise SystemExit(130)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--ignore-sigterm" in args:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
    else:
        signal.signal(signal.SIGTERM, _handle_sigterm)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SystemExit(91)

    if args:
        _write_prompt_echo(args[-1])

    sys.stdout.write("\x1b[?25l\r\x1b[2Kclaude tool: read_file\n")
    sys.stdout.flush()
    sys.stdout.write("Claude session ready. Session ID: pty-session-e2e\n")
    sys.stdout.flush()

    if "--sleep" in args:
        time.sleep(30)
    else:
        sys.stdout.write(
            "Task declared complete: session_id=pty-session-e2e, summary=done, timestamp=1\n"
        )
        sys.stdout.flush()
