from __future__ import annotations

import threading

from ralph.agents.invoke._pty_line_reader import PtyLineReader


def test_record_transcript_session_id_ignores_visible_tui_session_id_assignment() -> None:
    reader = PtyLineReader.__new__(PtyLineReader)
    reader._transcript_session_ids = []
    reader._transcript_session_ids_lock = threading.Lock()

    reader._record_transcript_session_id("assistant said session_id=fake-visible")

    assert reader._transcript_session_id_candidates() == ()
