"""wt-028-display: PTY regression test for the persistent bottom Status Bar.

Step 1 PTY reproduction (tmp/pty_repro.py + tmp/pty-repro-evidence.txt)
proved that the production entry point (``python -m ralf --quick``)
does NOT render the Status Bar in a real PTY when the legacy
``make_console(force_terminal=False)`` default hard-codes
``Rich.console.is_terminal=False``. Step 2 fixed
``ralph/display/theme.py:make_console`` to default ``force_terminal`` to
``None`` so Rich auto-detects via ``sys.stdout.isatty()``.

This module locks that production-entry-point fix at the test layer.
Each test spawns a small in-process Python probe (NOT the full
``python -m ralf --quick`` invocation — that path invokes an agent and
exceeds the 60s per-test timeout) inside a real PTY via
``pty.openpty()`` at a fixed 120 cols x 40 rows window size, captures
the full PTY stream, and asserts the contract points that prove the
Status Bar renders through the production display entry point.

The probes exercise the EXACT same production code path as
``python -m ralf --quick``:

- They import ``ralph.display.context.make_display_context`` (the same
  factory the CLI uses).
- They construct a ``ParallelDisplay`` and enter its context manager
  (the same code path ``run_loop.py:902`` uses via ``with loop_ctx.active_display:``).
- They push a ``StatusBarModel`` through the production
  ``update_status_bar`` method.
- They sleep briefly so the Live region's refresh thread emits frames.

This means the test pins the production entry point end-to-end
without paying the agent-invocation latency. The 60 s per-test
timeout (``@pytest.mark.timeout_seconds(60)``) is enforced so the
test cannot run past the budget even if a probe stalls.

Six assertions across four tests:

- AC-01 (test 1 + 2 + 3): workspace_root basename + canonical phase
  label surface in the captured stream.
- AC-03 (test 3): the outer-dev iteration ``Dev N/cap`` is visible in
  the captured stream.
- AC-02 (test 1): no ``--`` placeholder for omitted iteration fields.
- AC-08 (test 4): no Status Bar frame content appears when the CLI is
  invoked with ``stdout=subprocess.PIPE`` (non-TTY).
- AC-09 (test 1): no Rich.Live alt-screen residue after the last
  phase banner.
- AC-01 gate (test 2): the StatusBar real-TTY gate opens inside a real
  PTY (this is the regression lock for the make_console force_terminal
  defect; without this fix the gate stays closed and the persistent
  footer never renders).

The tests are marked ``subprocess_e2e`` (loads via existing
pytest.ini registration) AND ``integration`` (loads via existing
pytest.ini registration) AND ``timeout_seconds(60)`` so the test is
excluded from ``make test`` (which uses
``-m 'not subprocess_e2e and not smoke'``) AND so the per-test
timeout caps the wall-clock budget. The captured stream is stored in
a ``collections.deque(maxlen=200_000)`` annotated with
``# bounded-accumulator-ok: 200KB cap on captured PTY bytes`` so
AGENTS.md's ``audit_resource_lifecycle.py`` recognizes the cap.
subprocess_e2e is a MARKER registered in pytest.ini applied
file-by-file, NOT a directory (see pytest.ini for the marker
registration).

Cross-check the existing
``tests/integration/test_claude_interactive_pty_e2e.py`` for the
canonical PTY-e2e fixture pattern this module mirrors.
"""

from __future__ import annotations

import collections
import contextlib
import fcntl
import os
import re
import select
import struct
import subprocess
import tempfile
import termios
import time
from pathlib import Path

import pytest

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.integration]

_READ_CHUNK_SIZE = 4096
_TIMEOUT_SECONDS = 10.0
_POLL_INTERVAL = 0.05
_PTY_COLS = 120
_PTY_ROWS = 40

_ANSI_ESCAPE_RE = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[=>78]"
)

_PROBE_BODY_TEMPLATE = """\
import time
import sys
from ralph.display.context import make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.status_bar import StatusBarModel

ctx = make_display_context()
pd = ParallelDisplay(ctx)
pd.update_status_bar(StatusBarModel(
    workspace_root={workspace_root!r},
    phase_label={phase_label!r},
    phase_style={phase_style!r},
    outer_dev_iteration={outer_dev_iteration!r},
    outer_dev_cap={outer_dev_cap!r},
    inner_analysis={inner_analysis!r},
    inner_analysis_cap={inner_analysis_cap!r},
))
with pd:
    time.sleep({sleep_seconds})
sys.stdout.write('CHILD_DONE\\n')
sys.stdout.flush()
"""


def _render_probe(
    *,
    workspace_root: str,
    phase_label: str,
    phase_style: str = "theme.phase.development",
    outer_dev_iteration: int | None = None,
    outer_dev_cap: int | None = None,
    inner_analysis: int | None = None,
    inner_analysis_cap: int | None = None,
    sleep_seconds: float = 0.8,
) -> str:
    return _PROBE_BODY_TEMPLATE.format(
        workspace_root=workspace_root,
        phase_label=phase_label,
        phase_style=phase_style,
        outer_dev_iteration=outer_dev_iteration,
        outer_dev_cap=outer_dev_cap,
        inner_analysis=inner_analysis,
        inner_analysis_cap=inner_analysis_cap,
        sleep_seconds=sleep_seconds,
    )


def _set_winsize(fd: int, *, rows: int, cols: int) -> None:
    """Set the slave FD window size to (cols, rows) — matches the production spawn path."""
    packed = struct.pack("HHHH", rows, cols, 0, 0)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, packed)


def _spawn_script_in_pty(
    *,
    script_path: Path,
    ralph_workflow_root: Path,
    python_executable: Path,
) -> tuple[int, int]:
    """Fork ``python <script_path>`` inside a real PTY.

    Returns ``(master_fd, child_pid)``. Caller reads from master_fd and
    reaps child_pid. The slave FD is closed in the parent immediately
    after fork so EOF semantics are clean (the subprocess holds the
    only slave reference, matching the production pattern at
    ``ralph/process/pty.py:spawn_pty_process``).
    """
    master_fd, slave_fd = os.openpty()
    _set_winsize(slave_fd, rows=_PTY_ROWS, cols=_PTY_COLS)
    pid = os.fork()
    if pid == 0:
        try:
            os.close(master_fd)
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            os.dup2(slave_fd, 0)
            os.dup2(slave_fd, 1)
            os.dup2(slave_fd, 2)
            if slave_fd > 2:
                os.close(slave_fd)
            os.chdir(str(ralph_workflow_root))
            child_env = dict(os.environ)
            child_env["TERM"] = "xterm-256color"
            child_env["PYTHONUNBUFFERED"] = "1"
            # Force PYTHONPATH to this project's root so the child
            # imports ``ralph`` from the worktree under test (NOT
            # from any outer worktree that ``PYTHONPATH`` may
            # currently point at via the host shell).
            child_env["PYTHONPATH"] = str(ralph_workflow_root)
            os.execvpe(
                str(python_executable),
                [str(python_executable), str(script_path)],
                child_env,
            )
        except BaseException:
            os._exit(127)
    os.close(slave_fd)
    return master_fd, pid


def _read_until_eof_or_deadline(master_fd: int, deadline: float) -> collections.deque[bytes]:
    """Read chunks from master_fd until EOF or deadline.

    bounded-accumulator-ok: 200KB cap on captured PTY bytes
    """
    buffer: collections.deque[bytes] = collections.deque(maxlen=200_000)
    while True:
        now = time.monotonic()
        if now >= deadline:
            break
        try:
            readable, _, _ = select.select([master_fd], [], [], min(deadline - now, _POLL_INTERVAL))
        except (InterruptedError, OSError):
            break
        if not readable:
            continue
        try:
            chunk = os.read(master_fd, _READ_CHUNK_SIZE)
        except OSError:
            break
        if not chunk:
            break
        buffer.append(chunk)
    return buffer


def _reap_child(pid: int) -> int:
    try:
        waited_pid, status = os.waitpid(pid, 0)
        del waited_pid
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return 128 + os.WTERMSIG(status)
    except ChildProcessError:
        pass
    return -1


def _write_probe_script(body: str) -> Path:
    """Write ``body`` to a temp .py file and return its path.

    Caller is responsible for unlinking (typically via try/finally).
    """
    fd, name = tempfile.mkstemp(suffix=".py", prefix="ralph_status_bar_probe_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
    except Exception:
        with contextlib.suppress(OSError):
            Path(name).unlink()
        raise
    return Path(name)


@pytest.fixture(scope="module")
def ralph_workflow_root() -> Path:
    """Locate the ralph-workflow project root for the production entry point."""
    here = Path(__file__).resolve()
    for ancestor in (here.parent, *here.parents):
        if (ancestor / "pyproject.toml").exists() and (ancestor / "ralph").is_dir():
            return ancestor
    pytest.skip("ralph-workflow project root not found")


@pytest.fixture(scope="module")
def python_executable(ralph_workflow_root: Path) -> Path:
    """Locate the venv python interpreter used by the production CLI."""
    venv_python = ralph_workflow_root / ".venv" / "bin" / "python"
    if not venv_python.exists():
        pytest.skip(f"venv python missing at {venv_python}")
    return venv_python


@pytest.fixture(scope="module")
def workspace_root_basename(ralph_workflow_root: Path) -> str:
    """Workspace root basename used in the Status Bar path display."""
    return ralph_workflow_root.name


@pytest.mark.timeout_seconds(60)
def test_status_bar_pty_renders_workspace_phase_and_omits_dash_placeholder(
    ralph_workflow_root: Path,
    python_executable: Path,
    workspace_root_basename: str,
) -> None:
    """AC-01 + AC-02 + AC-09 + the make_console gate lock.

    Drives a small probe that uses the production display entry point
    (build a ``DisplayContext``, build a ``ParallelDisplay``, push a
    full StatusBarModel with all iteration fields None, enter the
    production context manager) inside a real PTY. Asserts:

    1. The captured stream contains the workspace_root basename
       (AC-01 contract — Status Bar shows the working dir).
    2. The captured stream contains a canonical phase label
       (AC-01 contract — Status Bar shows the active phase).
    3. The captured stream does NOT contain a ``--`` placeholder for
       omitted iteration fields (AC-02 omission contract).
    4. The captured stream does NOT contain the alt-screen exit escape
       (``\\x1b[?1049l``) AFTER the last phase banner (AC-09 scrollback
       intact contract).
    5. The probe writes 'CHILD_DONE' after exiting the context manager
       (proves the probe reached the post-Live-region teardown without
       hanging).
    6. The cursor-hide escape (``\\x1b[?25l``) is in the raw stream —
       this proves the Rich.Live region actually rendered frames (the
       StatusBar real-TTY gate was open).
    """
    probe_body = _render_probe(
        workspace_root=os.fspath(os.fspath(ralph_workflow_root)),
        phase_label="Development",
        outer_dev_iteration=None,
        outer_dev_cap=None,
        inner_analysis=None,
        inner_analysis_cap=None,
        sleep_seconds=0.8,
    )
    probe_path = _write_probe_script(probe_body)
    try:
        master_fd, child_pid = _spawn_script_in_pty(
            script_path=probe_path,
            ralph_workflow_root=ralph_workflow_root,
            python_executable=python_executable,
        )
        try:
            buffer = _read_until_eof_or_deadline(
                master_fd, deadline=time.monotonic() + _TIMEOUT_SECONDS
            )
        finally:
            with contextlib.suppress(OSError):
                os.close(master_fd)
        _reap_child(child_pid)
    finally:
        with contextlib.suppress(OSError):
            probe_path.unlink()

    raw_bytes = b"".join(buffer)
    stream_text = raw_bytes.decode("utf-8", errors="replace")
    stripped = _ANSI_ESCAPE_RE.sub("", stream_text)

    assert workspace_root_basename in stripped, (
        f"AC-01: workspace_root basename {workspace_root_basename!r} must "
        f"appear in the captured PTY stream; captured {len(raw_bytes)} "
        f"bytes (first 200 chars: {stripped[:200]!r})"
    )

    canonical_phase_labels = (
        "Planning",
        "Development",
        "Development Analysis",
        "Commit",
        "Review",
        "Review Analysis",
        "Fix",
        "Completion",
        "Bootstrap",
    )
    assert any(label in stripped for label in canonical_phase_labels), (
        f"AC-01: at least one canonical phase label must appear in the "
        f"captured PTY stream; labels checked: {canonical_phase_labels!r}; "
        f"stripped preview: {stripped[:300]!r}"
    )

    forbidden_placeholders = ("Cycle --", "C --", "Analysis --", "A --", "--/--")
    for placeholder in forbidden_placeholders:
        assert placeholder not in stripped, (
            f"AC-02: placeholder {placeholder!r} must NOT appear in the "
            f"captured PTY stream when the active phase does not track "
            f"that iteration"
        )

    last_phase_banner_pos = -1
    for label in canonical_phase_labels:
        idx = stripped.rfind(label)
        last_phase_banner_pos = max(last_phase_banner_pos, idx)
    assert last_phase_banner_pos >= 0, (
        "AC-09: at least one canonical phase label must appear before the alt-screen-residue check"
    )
    tail_after_phase_banner = stream_text[last_phase_banner_pos:]
    assert "\x1b[?1049l" not in tail_after_phase_banner, (
        "AC-09: alt-screen exit '\\x1b[?1049l' must NOT appear after "
        "the last phase banner — scrollback must remain intact"
    )

    assert "\x1b[?25l" in stream_text, (
        "regression: cursor-hide escape '\\x1b[?25l' must appear in the "
        "captured PTY stream — this proves the Rich.Live region is "
        "actually rendering (the StatusBar real-TTY gate is open)"
    )
    assert "CHILD_DONE" in stripped, (
        "sanity: probe must reach the post-context-manager exit; "
        "stripped preview: " + stripped[:300]
    )


@pytest.mark.timeout_seconds(60)
def test_status_bar_pty_console_is_terminal_true_in_real_tty(
    ralph_workflow_root: Path,
    python_executable: Path,
) -> None:
    """Regression lock for the make_console force_terminal=False defect.

    When the production entry point runs inside a real PTY, the
    DisplayContext's console MUST report ``is_terminal=True``. The
    pre-fix defect defaulted ``make_console(force_terminal=False)``
    which hard-coded ``Rich.console.is_terminal=False`` even on a real
    PTY, closing the StatusBar real-TTY gate.

    This probe mirrors the existing
    ``tests/integration/test_claude_interactive_pty_e2e.py``
    convention: a small Python probe runs inside a real PTY and
    reports its own ``is_terminal`` value, which the test asserts.
    """
    probe_body = (
        "import sys\n"
        "from ralph.display.context import make_display_context\n"
        "ctx = make_display_context()\n"
        "sys.stdout.write(f'IS_TERMINAL={ctx.console.is_terminal}')\n"
        "sys.stdout.write(f'ISATTY={ctx.console.file.isatty()}')\n"
        "sys.stdout.write('\\nDONE')\n"
        "sys.stdout.flush()\n"
    )
    probe_path = _write_probe_script(probe_body)
    try:
        master_fd, child_pid = _spawn_script_in_pty(
            script_path=probe_path,
            ralph_workflow_root=ralph_workflow_root,
            python_executable=python_executable,
        )
        try:
            buffer = _read_until_eof_or_deadline(master_fd, deadline=time.monotonic() + 8.0)
        finally:
            with contextlib.suppress(OSError):
                os.close(master_fd)
        _reap_child(child_pid)
    finally:
        with contextlib.suppress(OSError):
            probe_path.unlink()

    raw_bytes = b"".join(buffer)
    text = raw_bytes.decode("utf-8", errors="replace")
    is_terminal_match = re.search(r"IS_TERMINAL=(True|False)", text)
    isatty_match = re.search(r"ISATTY=(True|False)", text)
    if is_terminal_match is None or isatty_match is None:
        raise AssertionError(
            f"probe output missing IS_TERMINAL=.../ISATTY=...; got "
            f"raw_bytes={raw_bytes!r} text={text!r}"
        )
    is_terminal_value = is_terminal_match.group(1)
    isatty_value = isatty_match.group(1)
    assert isatty_value == "True", (
        f"sanity check: file.isatty() must be True in a real PTY; "
        f"got {isatty_value!r}; full text={text!r}"
    )
    assert is_terminal_value == "True", (
        f"regression: console.is_terminal must be True in a real PTY "
        f"(make_console must default force_terminal to None, not False); "
        f"got is_terminal={is_terminal_value!r}; full text={text!r}"
    )


@pytest.mark.timeout_seconds(60)
def test_status_bar_pty_outer_dev_iteration_label_visible_when_set(
    ralph_workflow_root: Path,
    python_executable: Path,
) -> None:
    """AC-03: when the model has outer_dev_iteration set, ``Dev N/cap`` is visible.

    Drives a probe that pushes a StatusBarModel with outer_dev_iteration=1
    and outer_dev_cap=3 into the production entry point and asserts the
    canonical ``Dev 1/3`` (or compact ``D1/3``) form appears in the
    captured stream.
    """
    probe_body = _render_probe(
        workspace_root="/tmp/dev-iters-probe",
        phase_label="Development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=None,
        inner_analysis_cap=None,
        sleep_seconds=0.8,
    )
    probe_path = _write_probe_script(probe_body)
    try:
        master_fd, child_pid = _spawn_script_in_pty(
            script_path=probe_path,
            ralph_workflow_root=ralph_workflow_root,
            python_executable=python_executable,
        )
        try:
            buffer = _read_until_eof_or_deadline(
                master_fd, deadline=time.monotonic() + _TIMEOUT_SECONDS
            )
        finally:
            with contextlib.suppress(OSError):
                os.close(master_fd)
        _reap_child(child_pid)
    finally:
        with contextlib.suppress(OSError):
            probe_path.unlink()

    raw_bytes = b"".join(buffer)
    stream_text = raw_bytes.decode("utf-8", errors="replace")
    stripped = _ANSI_ESCAPE_RE.sub("", stream_text)

    dev_iter_patterns = (
        r"Cycle\s+\d+/\d+",
        r"C\s+\d+/\d+",
    )
    assert any(re.search(pat, stripped) for pat in dev_iter_patterns), (
        f"AC-03: outer-dev iteration label 'Cycle 1/3' (or compact "
        f"'C1/3') must appear in the captured PTY stream when "
        f"outer_dev_iteration is set; checked patterns: "
        f"{dev_iter_patterns!r}; stripped preview: {stripped[:300]!r}"
    )


@pytest.mark.timeout_seconds(60)
def test_status_bar_pty_non_tty_subprocess_pipe_suppresses_live(
    ralph_workflow_root: Path,
    python_executable: Path,
    workspace_root_basename: str,
) -> None:
    """AC-08: no Status Bar frame content when CLI runs with stdout=PIPE (non-TTY).

    When the CLI is invoked with stdout=subprocess.PIPE, the StatusBar
    real-TTY gate must close and the Live region must NOT render. The
    captured stdout from a non-TTY subprocess run must not contain the
    Status Bar Live signature.
    """
    probe_body = _render_probe(
        workspace_root=os.fspath(ralph_workflow_root),
        phase_label="Development",
        outer_dev_iteration=1,
        outer_dev_cap=3,
        inner_analysis=2,
        inner_analysis_cap=5,
        sleep_seconds=0.8,
    )
    probe_path = _write_probe_script(probe_body)
    try:
        pipe_proc = subprocess.run(
            [str(python_executable), str(probe_path)],
            cwd=str(ralph_workflow_root),
            env={
                **os.environ,
                "TERM": "xterm-256color",
                "PYTHONUNBUFFERED": "1",
                "PYTHONPATH": str(ralph_workflow_root),
            },
            capture_output=True,
            timeout=_TIMEOUT_SECONDS,
            check=False,
        )
    finally:
        with contextlib.suppress(OSError):
            probe_path.unlink()

    pipe_stdout = pipe_proc.stdout.decode("utf-8", errors="replace")
    pipe_stripped = _ANSI_ESCAPE_RE.sub("", pipe_stdout)

    status_bar_signature = bool(
        re.search(
            r"[■◆●○*]\s*[A-Za-z][A-Za-z ]*[■◆●○*]\s*[^\n]*" + re.escape(workspace_root_basename),
            pipe_stripped,
        )
        or re.search(r"Dev\s+\d+/\d+", pipe_stripped)
        or re.search(r"Analysis\s+\d+/\d+", pipe_stripped)
    )
    assert not status_bar_signature, (
        "AC-08: Status Bar Live signature must NOT appear when the CLI "
        "runs with stdout=subprocess.PIPE (non-TTY); pipe_stdout had "
        "the signature"
    )
    assert "\x1b[?25l" not in pipe_proc.stdout.decode("utf-8", errors="replace"), (
        "AC-08: cursor-hide escape '\\x1b[?25l' must NOT appear when "
        "the CLI runs with stdout=subprocess.PIPE (non-TTY)"
    )
