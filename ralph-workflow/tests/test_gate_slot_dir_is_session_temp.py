from __future__ import annotations

import os
import tempfile
from pathlib import Path

from ralph.pipeline.publication_gate_slots import GATE_SLOT_DIR_ENV


def test_the_gate_slot_directory_lives_in_the_session_temp_that_is_removed() -> None:
    slot_dir = Path(os.environ[GATE_SLOT_DIR_ENV])
    session_root = Path(tempfile.gettempdir())

    assert slot_dir.is_dir()
    assert slot_dir.is_relative_to(session_root), (
        f"{slot_dir} outlives the session: only {session_root} is removed when it ends"
    )
