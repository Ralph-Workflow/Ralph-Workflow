"""Nanocoder interactive output parser."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.display.vt_normalizer import normalize_vt_text

from .agent_output_line import AgentOutputLine
from .generic import GenericParser

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry

_MAX_STATUS_EVENTS = 8
_TURN_BOUNDARY_MARKER = "[claude turn boundary]"
_CTX_PERCENT_RE = re.compile(r"\bctx:\s*\d+%")
_WHITESPACE_RE = re.compile(r"\s+")


def _status_signature(text: str) -> str:
    stable = _CTX_PERCENT_RE.sub("ctx:%", text)
    return _WHITESPACE_RE.sub(" ", stable).strip()


class NanocoderParser(GenericParser):
    """Parser for Nanocoder's interactive PTY stream.

    Nanocoder redraws a terminal UI while it works. Many PTY frames contain
    only cursor movement, erase codes, or whitespace after VT normalization.
    The generic parser turns those frames into visible ``raw`` events, which
    floods operator output. Nanocoder keeps GenericParser's plain-text tool
    marker support, but suppresses control-only frames.
    """

    def __init__(
        self,
        subagent_pid_registry: SubagentPidRegistry | None = None,
        subagent_source_label: str | None = None,
    ) -> None:
        super().__init__(
            subagent_pid_registry=subagent_pid_registry,
            subagent_source_label=subagent_source_label,
        )
        self._status_signatures: tuple[str, ...] = ()

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        normalized = normalize_vt_text(stripped).strip()
        yield from self._flush_accumulator()
        if not normalized:
            return
        if normalized == _TURN_BOUNDARY_MARKER:
            return
        if normalized.startswith("[plain] tool:"):
            yield from super()._classify_non_json_line(stripped)
            return
        signature = _status_signature(normalized)
        if signature in self._status_signatures:
            return
        if len(self._status_signatures) >= _MAX_STATUS_EVENTS:
            return
        self._status_signatures = (*self._status_signatures, signature)
        yield AgentOutputLine(
            type="status",
            content=normalized,
            raw=stripped,
            metadata={"event": "interactive_tui"},
        )
