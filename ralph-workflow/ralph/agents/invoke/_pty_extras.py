from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@dataclass(frozen=True)
class PtyExtras:
    expected_session_id: str | None = None
    stop_sentinel_path: Path | None = None
    permission_prompt_listener: Callable[[str], None] | None = None
    input_prompt: str | None = None
    # wt-04-claude-parsing: the ``*.jsonl`` transcript names already on
    # disk for the workspace, snapshotted by the caller BEFORE the PTY
    # child process is spawned. ``PtyLineReader`` cannot take this
    # snapshot itself in ``__init__`` -- by the time it is constructed,
    # ``run_pty_and_read_lines`` has already spawned the child, which
    # may have already created its own transcript file, so a snapshot
    # taken inside ``PtyLineReader.__init__`` would self-exclude the
    # very file discovery needs to find. ``None`` (the default) means
    # "no pre-spawn snapshot was taken"; ``PtyLineReader`` then falls
    # back to snapshotting at construction time, which is late but
    # still strictly better than not excluding anything.
    pre_existing_transcript_names: frozenset[str] | None = None
