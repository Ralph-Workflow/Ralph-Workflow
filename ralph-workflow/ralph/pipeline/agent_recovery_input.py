"""All inputs required to determine whether and how to retry an agent invocation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.effects import InvokeAgentEffect


@dataclass(frozen=True)
class AgentRecoveryInput:
    """All inputs required to determine whether and how to retry an agent invocation."""

    exc: Exception
    attempt_index: int
    max_recovery_attempts: int
    effect: InvokeAgentEffect
    workspace_root: Path
    raw_output: list[str]
    rendered_output: list[str]
    extracted_session_id: str | None
    inactivity_error_type: type[Exception]
    # Stale-session framing metadata. APPENDED at the end (not reordered)
    # so existing positional construction of this dataclass keeps working
    # unchanged. All new fields default to ``None`` so un-updated call sites
    # stay valid. ``stale_session_id`` carries the rejected session id
    # captured from the prior attempt's state so the retry prompt can
    # name it; ``transport`` and ``model`` describe the runtime that
    # rejected the session id.
    stale_session_id: str | None = None
    transport: str | None = None
    model: str | None = None
