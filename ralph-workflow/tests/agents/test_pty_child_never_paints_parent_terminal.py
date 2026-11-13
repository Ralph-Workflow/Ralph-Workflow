from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import textwrap

import pytest

from ralph.display.line_sanitizer import sanitize_display_line
from ralph.process.manager import ProcessTerminationIssuer, SpawnOptions
from ralph.process.manager._process_manager import ProcessManager
from ralph.process.manager._process_manager_policy import ProcessManagerPolicy
from ralph.process.manager._pty_spawn_options import PtySpawnOptions
from ralph.process.pty import read_master_chunk, wait_for_master_readable


@pytest.mark.subprocess_e2e
def test_pty_child_runs_in_its_own_session_and_paints_nothing_on_the_parent(
    capfd: pytest.CaptureFixture[str],
) -> None:
    parent_sid = os.getsid(0)

    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )

    script = textwrap.dedent(
        """
        import os
        import sys
        import time
        sys.stdout.write('\\x1b[?1049h\\x1b[2Jhello-from-tui\\n')
        sys.stdout.flush()
        # Report our own session id so the parent can verify the child
        # actually got its own session (not the parent's). Write it BEFORE
        # sleeping so the parent read loop terminates quickly even on the
        # 1.0s per-test SIGALRM.
        sys.stdout.write('child_sid=' + str(os.getsid(0)) + chr(10))
        sys.stdout.flush()
        # Brief keep-alive so the parent's master_fd read sees both lines;
        # keep well under the 1.0s per-test SIGALRM cap.
        time.sleep(0.01)
        """
    )
    command = [sys.executable, "-c", script]
    pty_handle = pm.spawn_pty(command, PtySpawnOptions(cols=120, rows=24))
    master_fd = pty_handle.master_fd

    try:
        raw_chunks: list[bytes] = []
        deadline_s = 0.9
        import time as _time

        start = _time.monotonic()
        while _time.monotonic() - start < deadline_s:
            if not wait_for_master_readable(master_fd, timeout_seconds=0.02):
                continue
            chunk = read_master_chunk(master_fd, max_bytes=4096)
            if not chunk:
                break
            raw_chunks.append(chunk)
            captured = b"".join(raw_chunks)
            marker = b"child_sid="
            if marker in captured and b"\n" in captured.split(marker, maxsplit=1)[1]:
                break
        raw = b"".join(raw_chunks)

        assert b"\x1b[?1049h" in raw, (
            f"PTY child must emit the alternate-screen sequence into its own "
            f"pty master; got raw={raw!r}"
        )
        assert b"child_sid=" in raw, (
            f"child must report its own session id from inside the pty; got raw={raw!r}"
        )
        text = raw.decode("utf-8", errors="replace")
        marker = "child_sid="
        idx = text.index(marker) + len(marker)
        end = text.index(chr(10), idx)
        child_sid_str = text[idx:end].strip()
        assert child_sid_str.isdigit(), (
            f"child session id must be a digit string; got {child_sid_str!r}"
        )
        child_sid = int(child_sid_str)
        assert child_sid != parent_sid, (
            f"PTY child must run in its own session (os.setsid+TIOCSCTTY in "
            f"ralph/process/pty.py:spawn_pty_process); got child_sid="
            f"{child_sid} == parent_sid={parent_sid}"
        )

        sanitized = sanitize_display_line(raw)
        assert "hello-from-tui" in sanitized, (
            f"sanitized text must keep 'hello-from-tui' visible; got {sanitized!r}"
        )
        assert "\x1b" not in sanitized, (
            f"sanitized text must contain no ESC bytes; got {sanitized!r}"
        )

        cap = capfd.readouterr()
        combined = (cap.out or "") + (cap.err or "")
        assert "\x1b" not in combined, (
            f"parent captured fds must contain zero ESC bytes (the black-"
            f"screen / log-overwrite leak); got combined={combined!r}"
        )
    finally:
        try:
            pty_handle.terminate(issuer=ProcessTerminationIssuer.OPERATOR)
        finally:
            pty_handle.close()


@pytest.mark.subprocess_e2e
def test_child_without_explicit_stdin_sees_eof_not_the_terminal(
    capfd: pytest.CaptureFixture[str],
) -> None:
    pm = ProcessManager(
        policy=ProcessManagerPolicy(enable_zombie_reaper=False, log_events=False),
    )

    script = textwrap.dedent(
        """
        import sys
        # Print stdin contents; with DEVNULL this prints '', proving EOF.
        sys.stdout.write(repr(sys.stdin.read()))
        sys.stdout.flush()
        """
    )
    command = [sys.executable, "-c", script]
    handle = pm.spawn(
        command,
        SpawnOptions(stdout=subprocess.PIPE, stderr=subprocess.PIPE),
    )

    try:
        rc = handle.wait(timeout=5.0)
        assert rc == 0, (
            f"child must exit cleanly when stdin is DEVNULL and the script "
            f"reads EOF; got returncode={rc!r}"
        )

        stdout, _stderr = handle.communicate(timeout=5.0)
        assert stdout is not None, "child stdout must be drained; got None"
        text = stdout.decode("utf-8", errors="replace")
        assert text.strip() == "''", (
            f"child must read '' from stdin (EOF from DEVNULL); got stdout={text!r}"
        )

        cap = capfd.readouterr()
        combined = (cap.out or "") + (cap.err or "")
        assert "\x1b" not in combined, (
            f"parent captured fds must contain zero ESC bytes; got combined={combined!r}"
        )
    finally:
        try:
            handle.terminate(issuer=ProcessTerminationIssuer.OPERATOR)
        finally:
            with contextlib.suppress(Exception):
                handle.__exit__(None, None, None)
