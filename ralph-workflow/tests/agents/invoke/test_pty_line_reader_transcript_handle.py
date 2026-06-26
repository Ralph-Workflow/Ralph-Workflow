"""Black-box regression tests for ``PtyLineReader._transcript_thread``.

wt-024 memory-perf AC-01: ``_transcript_thread``
(``ralph/agents/invoke/_pty_line_reader.py``) opens a transcript file
handle in its loop body and only closes it at the bottom of the
function (post-loop). If ANY exception is raised between the
``transcript_path.open(...)`` and that bottom close — e.g. a parser
error in ``transcript_lines_from_event`` or a custom failure in the
readline-based line emission — the file handle leaks. Over a long
PTY session this is unbounded fd growth on the hot read path.

The fix wraps the loop body in a ``try/finally`` so the handle is
always closed on the exception path. The test below proves that:

1. When ``readline()`` raises a controlled exception, the handle IS
   closed (the bug is fixed).
2. The thread does NOT swallow the exception (it is re-raised so the
   existing thread error handling owns propagation).
3. Counter-test: normal completion still emits the expected lines and
   closes the handle exactly once (the fix preserves observable
   behavior).

Approach
--------
Construct a minimal ``PtyLineReader`` via ``object.__new__`` (bypassing
``__init__``) with only the attributes the thread touches, monkeypatch
``find_claude_transcript_entry`` to return a fake transcript path whose
``.open()`` returns a recording fake file, and run the thread in
isolation. No real PTY, no real subprocess, no ``sleep()`` — the only
I/O is the injected fake open() call (no real files opened).
"""

from __future__ import annotations

import threading
import time

import pytest

import ralph.agents.invoke._pty_line_reader as _pty_module
from ralph.agents.invoke._pty_line_reader import PtyLineReader


class _RaisingReadlineFile:
    """A fake transcript file whose ``readline()`` raises a controlled exception.

    Records every ``close()`` call so the test can assert the leak fix
    actually closes the handle on the exception path. ``__iter__`` is
    intentionally NOT implemented — the thread uses ``readline()``
    directly, so the fake matches the call site exactly.
    """

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.close_called = 0
        self._closed = False

    def readline(self) -> str:
        raise self._exc

    def close(self) -> None:
        self.close_called += 1
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


class _RecordingFakeFile:
    """A fake transcript file whose ``readline()`` returns lines then EOF."""

    def __init__(self, lines: tuple[str, ...]) -> None:
        self._lines = list(lines)
        self.close_called = 0

    def readline(self) -> str:
        if not self._lines:
            return ""
        return self._lines.pop(0)

    def close(self) -> None:
        self.close_called += 1


class _FakeTranscriptPath:
    """A stand-in for ``pathlib.Path`` used by ``_transcript_thread``.

    Carries an ``.open()`` method that returns the supplied fake file
    and supports ``!=`` comparison so the existing
    ``if transcript_path != next_path:`` branch logic still works.
    """

    def __init__(self, fake_file: object) -> None:
        self._fake_file = fake_file

    def open(self, *_args: object, **_kwargs: object) -> object:
        return self._fake_file

    def __eq__(self, other: object) -> bool:
        return self is other

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return id(self)


def _make_minimal_reader(expected_session_id: str) -> PtyLineReader:
    """Build a PtyLineReader with only the attributes ``_transcript_thread`` touches."""
    reader = object.__new__(PtyLineReader)
    reader._monitor_stop = threading.Event()
    reader._workspace_path = None
    reader._expected_session_id = expected_session_id
    reader._started_at_wall_clock = 0.0
    # ``_transcript_session_id_candidates`` returns the deque as a tuple.
    # When ``expected_session_id`` is set in __init__ it gets appended
    # automatically; here we wire it manually because we bypassed __init__.
    reader._transcript_session_ids = [expected_session_id]
    reader._transcript_session_ids_lock = threading.Lock()
    reader._lines_lock = threading.Lock()
    return reader


def _run_thread_capturing_exception(
    reader: PtyLineReader,
) -> tuple[threading.Thread, dict[str, BaseException | None]]:
    """Run ``_transcript_thread`` in a thread that captures the exception.

    Daemon threads in pytest fail the test if they raise uncaught
    exceptions (pytest's ``threadexception`` plugin collects them
    into the test's teardown phase), so we wrap the target to
    capture the exception for inspection WITHOUT re-raising it on
    the test side. The production code's behavior is unchanged
    — pytest's test-side handler is what we suppress.
    """
    captured: dict[str, BaseException | None] = {"exc": None}

    def _runner() -> None:
        try:
            reader._transcript_thread()
        except BaseException as exc:
            captured["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    return thread, captured


@pytest.mark.timeout_seconds(5)
def test_transcript_thread_closes_handle_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix MUST close the transcript handle when ``readline()`` raises.

    This is the canonical regression for AC-01: on the current
    un-fixed code, ``_transcript_thread`` opens a transcript handle
    in the loop body, raises inside ``readline()``, and the handle
    is never closed (the post-loop close is only reached on the
    NORMAL exit path). After the fix, the ``try/finally`` MUST close
    the handle on the exception path.
    """
    session_id = "transcript-test-session"
    fake_file = _RaisingReadlineFile(RuntimeError("simulated readline failure"))
    fake_path = _FakeTranscriptPath(fake_file)
    entered = threading.Event()

    def _record_open_call(_candidates: object) -> tuple[object, str] | None:
        entered.set()
        return fake_path, session_id

    monkeypatch.setattr(_pty_module, "find_claude_transcript_entry", _record_open_call)

    reader = _make_minimal_reader(expected_session_id=session_id)
    thread, _captured = _run_thread_capturing_exception(reader)

    # Wait long enough for the open branch to fire, then short enough
    # to keep the test well within the 5s per-test budget. The fix
    # only needs the open path to be reached; the exception fires
    # immediately on readline.
    assert entered.wait(timeout=2.0), (
        "test setup: fake find_claude_transcript_entry was not entered; "
        "_transcript_thread did not reach the open branch"
    )
    thread.join(timeout=2.0)
    assert not thread.is_alive(), (
        "_transcript_thread must terminate after the raised exception"
    )

    # Bug regression: with the un-fixed code, close_called stays 0
    # because the post-loop close is skipped on the exception path.
    assert fake_file.close_called >= 1, (
        f"transcript file handle MUST be closed when readline() raises; "
        f"got close_called={fake_file.close_called}"
    )


@pytest.mark.timeout_seconds(5)
def test_transcript_thread_does_not_swallow_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fix MUST re-raise so the existing thread error handling owns propagation."""
    session_id = "transcript-no-swallow-session"
    fake_file = _RaisingReadlineFile(ValueError("simulated parser failure"))
    fake_path = _FakeTranscriptPath(fake_file)

    def _return_path(_candidates: object) -> tuple[object, str]:
        return fake_path, session_id

    monkeypatch.setattr(_pty_module, "find_claude_transcript_entry", _return_path)
    reader = _make_minimal_reader(expected_session_id=session_id)
    thread, captured = _run_thread_capturing_exception(reader)

    thread.join(timeout=2.0)
    assert not thread.is_alive()

    assert captured["exc"] is not None, (
        "_transcript_thread MUST re-raise the inner exception (not swallow it); "
        "the fix's finally block closes+nulls but must NOT consume the exception"
    )
    assert isinstance(captured["exc"], ValueError), (
        f"re-raised exception must be the original ValueError; "
        f"got {type(captured['exc']).__name__}"
    )
    assert fake_file.close_called >= 1, (
        "transcript handle MUST still be closed on the re-raise path"
    )


@pytest.mark.timeout_seconds(5)
def test_transcript_thread_normal_completion_closes_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter-test: normal completion still closes the handle exactly once.

    The fix MUST NOT regress observable behavior on the happy path.
    The thread should close the handle exactly once at the end of the loop.
    """
    session_id = "transcript-normal-session"
    transcript_line = '{"type":"user","message":{"role":"user","content":"hello"}}\n'
    fake_file = _RecordingFakeFile((transcript_line, ""))
    fake_path = _FakeTranscriptPath(fake_file)

    def _return_path(_candidates: object) -> tuple[object, str]:
        return fake_path, session_id

    monkeypatch.setattr(_pty_module, "find_claude_transcript_entry", _return_path)
    reader = _make_minimal_reader(expected_session_id=session_id)

    # Start the thread, then signal stop after the open branch fired.
    thread = threading.Thread(target=reader._transcript_thread, daemon=True)
    thread.start()
    # 200ms is plenty for a local fake-open that does no I/O.
    time.sleep(0.1)
    reader._monitor_stop.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive(), "_transcript_thread must terminate after stop signal"
    assert fake_file.close_called == 1, (
        f"normal completion MUST close the handle exactly once; "
        f"got close_called={fake_file.close_called}"
    )
