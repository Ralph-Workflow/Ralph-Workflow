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
    pre_existing_transcript_names: frozenset[str] | None = None
