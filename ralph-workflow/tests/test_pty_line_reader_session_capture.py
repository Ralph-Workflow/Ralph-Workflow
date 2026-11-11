from __future__ import annotations

import threading
from collections import deque

from ralph.agents.invoke._pty_helpers import (
    _MAX_TRANSCRIPT_SESSION_IDS,
    _RECENT_CHOICE_LINES_MAX,
)
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock


def _make_minimal_reader() -> PtyLineReader:
    reader = PtyLineReader.__new__(PtyLineReader)
    reader._transcript_session_ids = deque(maxlen=_MAX_TRANSCRIPT_SESSION_IDS)
    reader._transcript_session_ids_lock = threading.Lock()
    reader._captured_session_id = None
    reader._recent_choice_lines = deque(maxlen=_RECENT_CHOICE_LINES_MAX)
    reader._clock = FakeClock(start=0.0)
    reader._auto_mode_prompt_seen = False
    reader._auto_response_menu_seen = False
    reader._auto_mode_menu_screen = None
    reader._last_auto_mode_response_at = None
    reader._last_auto_mode_menu_seen_at = None
    reader._pending_permission_prompt_line = None
    reader._pending_permission_prompt_started_at = None
    return reader


def _session_id_line(session_id: str) -> str:
    return f"Claude session ready. Session ID: {session_id}"


def test_record_transcript_session_id_ignores_lines_without_session_id() -> None:
    reader = _make_minimal_reader()

    reader._record_transcript_session_id("assistant said session_id=fake-visible")

    assert reader._transcript_session_id_candidates() == ()


def test_record_transcript_session_id_caps_at_max_size() -> None:
    reader = _make_minimal_reader()

    for index in range(_MAX_TRANSCRIPT_SESSION_IDS + 5):
        reader._record_transcript_session_id(_session_id_line(f"session-{index:04d}"))

    candidates = reader._transcript_session_id_candidates()
    assert len(candidates) == _MAX_TRANSCRIPT_SESSION_IDS
    assert candidates[0] == f"session-{_MAX_TRANSCRIPT_SESSION_IDS + 4:04d}"
    for index in range(5):
        assert f"session-{index:04d}" not in candidates


def test_record_transcript_session_id_dedup_moves_existing_to_front() -> None:
    reader = _make_minimal_reader()

    for sid in ("alpha", "beta", "gamma"):
        reader._record_transcript_session_id(_session_id_line(sid))
    assert tuple(reader._transcript_session_id_candidates()) == ("gamma", "beta", "alpha")

    reader._record_transcript_session_id(_session_id_line("alpha"))
    assert reader._transcript_session_id_candidates()[0] == "alpha"
    assert set(reader._transcript_session_id_candidates()) == {"alpha", "beta", "gamma"}


def test_observe_queued_line_caps_recent_choice_lines_at_max_size() -> None:
    reader = _make_minimal_reader()

    for index in range(_RECENT_CHOICE_LINES_MAX + 10):
        reader._observe_queued_line(f"line-{index}\n")

    assert len(reader._recent_choice_lines) == _RECENT_CHOICE_LINES_MAX
    snapshot = list(reader._recent_choice_lines)
    assert "line-0\n" not in snapshot
    assert snapshot[-1] == f"line-{_RECENT_CHOICE_LINES_MAX + 9}\n"


def test_session_capture_caches_use_deque_typed_storage() -> None:
    reader = _make_minimal_reader()
    assert isinstance(reader._recent_choice_lines, deque)
    assert reader._recent_choice_lines.maxlen == _RECENT_CHOICE_LINES_MAX
    assert isinstance(reader._transcript_session_ids, deque)
    assert reader._transcript_session_ids.maxlen == _MAX_TRANSCRIPT_SESSION_IDS
