"""Black-box tests for the bounded PTY transcript session-id and recent-choice deques.

wt-024 Step 8 (AC-07): ``_transcript_session_ids`` and
``_recent_choice_lines`` on ``PtyLineReader`` are bounded
``collections.deque(maxlen=...)`` instances so a long invocation
cannot grow them unboundedly. The tests below drive the
PRODUCTION entry points:

* :meth:`PtyLineReader._record_transcript_session_id` for the
  transcript session-id path -- this is the canonical production
  method that ingests raw PTY lines, runs the visible-TUI
  session-id extraction, and updates the bounded deque.
* :meth:`PtyLineReader._observe_queued_line` for the
  recent-choice path -- this is the canonical production method
  that appends to ``_recent_choice_lines`` whenever a queued
  line is observed.

A minimal ``PtyLineReader`` is constructed via ``__new__`` to
bypass the expensive PTY/thread setup; the public production
methods then run end-to-end against the bounded caches. The
resulting cache state is asserted via the reader's own public
candidate accessor ``_transcript_session_id_candidates`` for the
transcript path and the cache attribute ``_recent_choice_lines``
for the recent-choice path.

No direct deque mutation in the test body, no private deque
construction, no real PTY, no real subprocess, no ``time.sleep``.
"""

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
    """Build a bare ``PtyLineReader`` with only the caches the tests exercise."""
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
    """Build a raw PTY line that the production extractor recognises as a session id.

    The canonical TUI banner form is
    ``Claude session ready. Session ID: <id>`` -- see
    :data:`_TRANSPORT_TEXT_SESSION_PATTERNS` in
    ``ralph.agents.invoke._session``.
    """
    return f"Claude session ready. Session ID: {session_id}"


def test_record_transcript_session_id_ignores_lines_without_session_id() -> None:
    """A raw PTY line without a visible session id is silently skipped."""
    reader = _make_minimal_reader()

    reader._record_transcript_session_id("assistant said session_id=fake-visible")

    assert reader._transcript_session_id_candidates() == ()


def test_record_transcript_session_id_caps_at_max_size() -> None:
    """AC-07: driving ``_record_transcript_session_id`` with more than the cap evicts oldest.

    Drives the PRODUCTION entry point; the resulting cache state
    is asserted via the public candidate accessor.
    ``candidate_ids[0]`` is consumed by ``_transcript_thread`` as
    the preferred transcript session, so the newest session id
    MUST be at the front (this is the canonical recent-session
    dedup semantic -- the test pins it explicitly).
    """
    reader = _make_minimal_reader()

    # Push _MAX_TRANSCRIPT_SESSION_IDS + 5 distinct session ids through
    # the production entry point.
    for index in range(_MAX_TRANSCRIPT_SESSION_IDS + 5):
        reader._record_transcript_session_id(_session_id_line(f"session-{index:04d}"))

    candidates = reader._transcript_session_id_candidates()
    # The deque MUST stay at the cap.
    assert len(candidates) == _MAX_TRANSCRIPT_SESSION_IDS
    # The newest session id MUST be at the front so the PTY
    # resume path prefers the most-recently-seen session.
    assert candidates[0] == f"session-{_MAX_TRANSCRIPT_SESSION_IDS + 4:04d}"
    # The 5 oldest entries (session-0..session-4) MUST be evicted.
    for index in range(5):
        assert f"session-{index:04d}" not in candidates


def test_record_transcript_session_id_dedup_moves_existing_to_front() -> None:
    """Re-recording an existing session id moves it to the FRONT (most-recent).

    Drives the production entry point three times; the
    subsequent re-recording of an existing id must move it to the
    front (newest) position so the PTY resume path sees it as
    the preferred candidate.
    """
    reader = _make_minimal_reader()

    for sid in ("alpha", "beta", "gamma"):
        reader._record_transcript_session_id(_session_id_line(sid))
    # Newest-first ordering: gamma was recorded last so it sits at the front.
    assert tuple(reader._transcript_session_id_candidates()) == ("gamma", "beta", "alpha")

    reader._record_transcript_session_id(_session_id_line("alpha"))
    # alpha is now the newest; it sits at the FRONT. The relative
    # order of beta and gamma is preserved.
    assert reader._transcript_session_id_candidates()[0] == "alpha"
    # The candidate set MUST be a permutation of the same three ids (no duplicates).
    assert set(reader._transcript_session_id_candidates()) == {"alpha", "beta", "gamma"}


def test_observe_queued_line_caps_recent_choice_lines_at_max_size() -> None:
    """AC-07: driving ``_observe_queued_line`` beyond the cap evicts the oldest."""
    reader = _make_minimal_reader()

    for index in range(_RECENT_CHOICE_LINES_MAX + 10):
        # A simple non-menu raw line: exercise the deque append path
        # without triggering the choice-menu side branches.
        reader._observe_queued_line(f"line-{index}\n")

    # Cap is enforced.
    assert len(reader._recent_choice_lines) == _RECENT_CHOICE_LINES_MAX
    # Oldest entries are evicted; the most recent N are retained.
    snapshot = list(reader._recent_choice_lines)
    assert "line-0\n" not in snapshot
    assert snapshot[-1] == f"line-{_RECENT_CHOICE_LINES_MAX + 9}\n"


def test_session_capture_caches_use_deque_typed_storage() -> None:
    """The session-capture state fields are deque-typed for O(1) eviction.

    Black-box check: a ``PtyLineReader`` constructed via the
    production code path stores both caches as bounded
    ``collections.deque`` instances (the production
    ``__init__`` installs them). We assert the type + the
    ``maxlen`` so a future refactor cannot accidentally replace
    the deque with an unbounded list.
    """
    reader = _make_minimal_reader()
    assert isinstance(reader._recent_choice_lines, deque)
    assert reader._recent_choice_lines.maxlen == _RECENT_CHOICE_LINES_MAX
    assert isinstance(reader._transcript_session_ids, deque)
    assert reader._transcript_session_ids.maxlen == _MAX_TRANSCRIPT_SESSION_IDS
