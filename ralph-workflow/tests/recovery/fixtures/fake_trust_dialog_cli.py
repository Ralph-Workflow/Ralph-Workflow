from __future__ import annotations

import json
import os
import select
import sys
import time
from pathlib import Path

MARKER_FILENAME = "ralph_canary_marker.txt"
MARKER_CONTENT = "RALPH_CANARY_OK"
MISMATCHED_MARKER_CONTENT = "RALPH_CANARY_WRONG"
TRUSTED_ROOT_ENV = "STUB_TRUSTED_ROOT"
BLOCK_ENV = "STUB_BLOCK"
EXIT_DELAY_ENV = "STUB_EXIT_DELAY_SECONDS"
ARGV_RECORD_ENV = "STUB_ARGV_RECORD"
DIALOG_TEXT_ENV = "STUB_DIALOG_TEXT"
STDOUT_RECORD_ENV = "STUB_STDOUT_RECORD"
STDIN_RECORD_ENV = "STUB_STDIN_RECORD"
MARKER_MODE_ENV = "STUB_MARKER_MODE"
MARKER_RECORD_ENV = "STUB_MARKER_RECORD"

DIALOG_TAIL = "╭─ Vault handshake ─╮"

_STDIN_POLL_SECONDS = 0.2


def _within(candidate: Path, parent: Path) -> bool:
    try:
        candidate.relative_to(parent)
    except ValueError:
        return False
    return True


def _record_argv() -> None:
    record_path = os.environ.get(ARGV_RECORD_ENV)
    if not record_path:
        return
    Path(record_path).write_text(json.dumps(sys.argv), encoding="utf-8")


def _mismatched_marker_mode() -> bool:
    return os.environ.get(MARKER_MODE_ENV) == "mismatch"


def _marker_content() -> str:
    if _mismatched_marker_mode():
        return MISMATCHED_MARKER_CONTENT
    return MARKER_CONTENT


def _record_marker(marker_path: Path, content: str, returncode: int) -> None:
    record_path = os.environ.get(MARKER_RECORD_ENV)
    if not record_path:
        return
    Path(record_path).write_text(
        json.dumps({"path": str(marker_path), "content": content, "returncode": returncode}),
        encoding="utf-8",
    )


def _emit(line: str) -> None:
    sys.stdout.write(line)
    sys.stdout.flush()
    record_path = os.environ.get(STDOUT_RECORD_ENV)
    if not record_path:
        return
    with Path(record_path).open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()


def _dialog_tail() -> str:
    return os.environ.get(DIALOG_TEXT_ENV) or DIALOG_TAIL


def _record_stdin(data: bytes) -> None:
    record_path = os.environ.get(STDIN_RECORD_ENV)
    if not record_path:
        return
    with Path(record_path).open("a", encoding="utf-8") as handle:
        handle.write(data.decode("utf-8", errors="replace"))
        handle.flush()


def _block_forever() -> None:
    while True:
        try:
            ready, _, _ = select.select([sys.stdin], [], [], _STDIN_POLL_SECONDS)
        except (OSError, ValueError):
            time.sleep(3600)
            return
        if not ready:
            continue
        try:
            data = os.read(sys.stdin.fileno(), 4096)
        except OSError:
            time.sleep(3600)
            return
        if not data:
            time.sleep(3600)
            return
        _record_stdin(data)


def main() -> int:
    if not os.isatty(0):
        _emit("fake-trust-dialog-cli: refusing to run without a TTY\n")
        return 2
    _record_argv()
    trusted_root = os.environ.get(TRUSTED_ROOT_ENV)
    cwd = Path.cwd()
    if trusted_root and _within(cwd, Path(trusted_root)):
        content = _marker_content()
        marker_path = cwd / MARKER_FILENAME
        marker_path.write_text(content, encoding="utf-8")
        _record_marker(marker_path, content, 0)
        if _mismatched_marker_mode():
            _emit("claude tool: Write\n")
        _emit("fake-trust-dialog-cli: trusted cwd, marker written\n")
        return 0
    _emit(f"{_dialog_tail()}\n")
    if os.environ.get(BLOCK_ENV) == "1":
        _block_forever()
    time.sleep(float(os.environ.get(EXIT_DELAY_ENV, "0.5")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
