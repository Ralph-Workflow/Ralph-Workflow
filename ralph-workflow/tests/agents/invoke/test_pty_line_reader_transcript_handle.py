from __future__ import annotations

import threading

import pytest

import ralph.agents.invoke._pty_line_reader as _pty_module
from ralph.agents.invoke._pty_line_reader import PtyLineReader


class _RaisingReadlineFile:
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
    def __init__(
        self,
        lines: tuple[str, ...],
        opened_signal: threading.Event | None = None,
    ) -> None:
        self._lines = list(lines)
        self.close_called = 0
        self._opened_signal = opened_signal

    def readline(self) -> str:
        if self._opened_signal is not None:
            self._opened_signal.set()
        if not self._lines:
            return ""
        return self._lines.pop(0)

    def close(self) -> None:
        self.close_called += 1


class _FakeTranscriptPath:
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
    reader = object.__new__(PtyLineReader)
    reader._monitor_stop = threading.Event()
    reader._workspace_path = None
    reader._expected_session_id = expected_session_id
    reader._started_at_wall_clock = 0.0
    reader._transcript_session_ids = [expected_session_id]
    reader._transcript_session_ids_lock = threading.Lock()
    reader._captured_session_id = None
    reader._lines_lock = threading.Lock()
    return reader


def _run_thread_capturing_exception(
    reader: PtyLineReader,
) -> tuple[threading.Thread, dict[str, BaseException | None]]:
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

    assert entered.wait(timeout=2.0), (
        "test setup: fake find_claude_transcript_entry was not entered; "
        "_transcript_thread did not reach the open branch"
    )
    thread.join(timeout=2.0)
    assert not thread.is_alive(), "_transcript_thread must terminate after the raised exception"

    assert fake_file.close_called >= 1, (
        f"transcript file handle MUST be closed when readline() raises; "
        f"got close_called={fake_file.close_called}"
    )


@pytest.mark.timeout_seconds(5)
def test_transcript_thread_does_not_swallow_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        f"re-raised exception must be the original ValueError; got {type(captured['exc']).__name__}"
    )
    assert fake_file.close_called >= 1, (
        "transcript handle MUST still be closed on the re-raise path"
    )


@pytest.mark.timeout_seconds(5)
def test_transcript_thread_normal_completion_closes_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "transcript-normal-session"
    transcript_line = '{"type":"user","message":{"role":"user","content":"hello"}}\n'
    opened_signal = threading.Event()
    fake_file = _RecordingFakeFile((transcript_line, ""), opened_signal=opened_signal)
    fake_path = _FakeTranscriptPath(fake_file)

    def _return_path(_candidates: object) -> tuple[object, str]:
        return fake_path, session_id

    monkeypatch.setattr(_pty_module, "find_claude_transcript_entry", _return_path)
    reader = _make_minimal_reader(expected_session_id=session_id)

    thread = threading.Thread(target=reader._transcript_thread, daemon=True)
    thread.start()
    assert opened_signal.wait(timeout=2.0), (
        "test setup: fake transcript file's readline() was not entered; "
        "_transcript_thread did not reach the open branch"
    )
    reader._monitor_stop.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive(), "_transcript_thread must terminate after stop signal"
    assert fake_file.close_called == 1, (
        f"normal completion MUST close the handle exactly once; "
        f"got close_called={fake_file.close_called}"
    )


@pytest.mark.timeout_seconds(5)
def test_transcript_thread_captures_session_id_from_discovered_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "transcript-discovered-session"
    opened_signal = threading.Event()
    fake_file = _RecordingFakeFile(("",), opened_signal=opened_signal)
    fake_path = _FakeTranscriptPath(fake_file)

    def _return_path(_candidates: object) -> tuple[object, str]:
        return fake_path, session_id

    monkeypatch.setattr(_pty_module, "find_claude_transcript_entry", _return_path)
    reader = _make_minimal_reader(expected_session_id=session_id)

    thread = threading.Thread(target=reader._transcript_thread, daemon=True)
    thread.start()
    assert opened_signal.wait(timeout=2.0)
    reader._monitor_stop.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()
    assert reader.captured_session_id == session_id


def test_late_transcript_discovery_captures_session_after_reader_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_id = "late-transcript-session"
    reader = _make_minimal_reader(expected_session_id=session_id)
    reader._workspace_path = None
    monkeypatch.setattr(
        _pty_module,
        "find_claude_transcript_entry",
        lambda _candidates: (_FakeTranscriptPath(_RecordingFakeFile(("",))), session_id),
    )

    assert reader.discover_captured_session_id() == session_id
    assert reader.captured_session_id == session_id
