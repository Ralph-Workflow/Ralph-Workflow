"""Shared text delta accumulator for parser implementations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .agent_output_line import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(slots=True)
class TextAccumulator:
    """Shared delta accumulator for paragraph-boundary text flushing."""

    buffer: str = ""
    raw_lines: list[str] = field(default_factory=list)

    def accumulate(
        self,
        text: str,
        raw: str,
        *,
        kind: str = "text",
        keep_current_when_empty: bool,
    ) -> Iterator[AgentOutputLine]:
        """Append text/raw and yield an AgentOutputLine if a paragraph boundary is reached.

        Args:
            text: Text delta to append to the buffer.
            raw: Raw JSON line to track.
            kind: Output type for the emitted line ('text' or 'thinking').
            keep_current_when_empty: When True, always keep the current raw line in the
                tail after a flush even if remaining buffer is empty (unconditional rule).
                When False, only keep it when remaining is non-empty.
        """
        self.buffer += text
        self.raw_lines.append(raw)
        if "\n\n" in self.buffer:
            parts = self.buffer.split("\n\n", 1)
            flushed = parts[0]
            remaining = parts[1]
            if flushed:
                flushed_raw = "\n".join(self.raw_lines[:-1])
                yield AgentOutputLine(type=kind, content=flushed, raw=flushed_raw)
            self.buffer = remaining
            self.raw_lines = [raw] if (remaining or keep_current_when_empty) else []

    def flush(
        self, *, kind: str = "text", require_strip: bool = False
    ) -> Iterator[AgentOutputLine]:
        """Yield remaining buffer content as an AgentOutputLine if non-empty, then reset.

        Args:
            kind: Output type for the emitted line ('text' or 'thinking').
            require_strip: When True, only emit if buffer.strip() is non-empty (for
                thinking accumulators that should suppress whitespace-only content).
        """
        check = self.buffer.strip() if require_strip else self.buffer
        if check:
            raw_joined = "\n".join(self.raw_lines) if self.raw_lines else ""
            yield AgentOutputLine(type=kind, content=self.buffer, raw=raw_joined)
        self.buffer = ""
        self.raw_lines = []
