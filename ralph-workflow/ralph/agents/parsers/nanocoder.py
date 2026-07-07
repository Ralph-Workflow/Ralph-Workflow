"""Nanocoder interactive output parser."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.display.vt_normalizer import normalize_vt_text

from .agent_output_line import AgentOutputLine
from .generic import GenericParser

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.agents.idle_watchdog import SubagentPidRegistry


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
        self._status_emitted = False

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        normalized = normalize_vt_text(stripped).strip()
        yield from self._flush_accumulator()
        if not normalized:
            return
        if normalized.startswith("[plain] tool:"):
            yield from super()._classify_non_json_line(stripped)
            return
        if self._status_emitted:
            return
        self._status_emitted = True
        yield AgentOutputLine(
            type="status",
            content="interactive output",
            raw=stripped,
            metadata={"event": "interactive_tui"},
        )
