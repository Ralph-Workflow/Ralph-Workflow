from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from ralph.agents.invoke.completion_quiesce_decision import CompletionQuiesceDecision
from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.artifacts.idempotent_write import atomic_write_text_if_changed

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

PARTIAL_CHILD_EDITS_PATH: Final[str] = ".agent/tmp/partial_child_edits.json"
CHILD_ACTIVITY_WINDOW_SECONDS: Final[float] = 60.0


@dataclass
class CompletionQuiesce:
    window_seconds: float
    deadline: float | None = None

    def decide(self, active_children: Sequence[str], now: float) -> CompletionQuiesceDecision:
        if not active_children:
            return CompletionQuiesceDecision.TEARDOWN
        if self.deadline is None:
            self.deadline = now + self.window_seconds
        if now < self.deadline:
            return CompletionQuiesceDecision.WAIT
        return CompletionQuiesceDecision.TEARDOWN_PARTIAL


def _marker(workspace: Path) -> Path:
    return workspace / PARTIAL_CHILD_EDITS_PATH


def record_partial_child_edits(workspace: Path, *, agent: str, children: Sequence[str]) -> None:
    marker = _marker(workspace)
    marker.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {"agent": agent, "children": [str(child) for child in children]}
    atomic_write_text_if_changed(
        DEFAULT_FILE_BACKEND,
        marker,
        json.dumps(payload, sort_keys=True),
        tmp_path=marker.with_suffix(".tmp"),
    )


def partial_child_edits(workspace: Path) -> tuple[str, ...]:
    try:
        raw: object = json.loads(_marker(workspace).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(raw, dict):
        return ()
    children: object = raw.get("children")
    if not isinstance(children, list):
        return ()
    return tuple(child for child in children if isinstance(child, str))


def clear_partial_child_edits(workspace: Path) -> None:
    _marker(workspace).unlink(missing_ok=True)


__all__ = [
    "CHILD_ACTIVITY_WINDOW_SECONDS",
    "PARTIAL_CHILD_EDITS_PATH",
    "CompletionQuiesce",
    "CompletionQuiesceDecision",
    "clear_partial_child_edits",
    "partial_child_edits",
    "record_partial_child_edits",
]
