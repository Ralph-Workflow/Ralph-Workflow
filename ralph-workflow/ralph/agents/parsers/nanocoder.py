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
_MAX_TEXT_EVENTS = 64
_TURN_BOUNDARY_MARKER = "[claude turn boundary]"
_CTX_PERCENT_RE = re.compile(r"\bctx:\s*\d+%")
_WHITESPACE_RE = re.compile(r"\s+")
_SPINNER_STATUS_RE = re.compile(r"^[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s+")
_MULTIPLICATION_SIGN = chr(215)
_EXECUTED_TOOL_RE = re.compile(
    rf"^⚒\s+Executed\s+(?P<tool>\S+)(?:\s+{_MULTIPLICATION_SIGN}\s+\d+)?$"
)


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
        self._text_signatures: tuple[str, ...] = ()

    def _text_seen(self, signature: str) -> bool:
        if signature in self._text_signatures:
            return True
        if len(self._text_signatures) >= _MAX_TEXT_EVENTS:
            self._text_signatures = (*self._text_signatures[1:], signature)
        else:
            self._text_signatures = (*self._text_signatures, signature)
        return False

    def _is_status_line(self, normalized: str) -> bool:
        if _SPINNER_STATUS_RE.search(normalized):
            return True
        lowered = normalized.lower()
        return (
            "waiting for mcp servers" in lowered
            or "waiting for chat to complete" in lowered
            or " · " in normalized
            or normalized.startswith("~/Library/Preferences/nanocoder/")
        )

    def _classify_non_json_line(self, stripped: str) -> Iterator[AgentOutputLine]:
        normalized = normalize_vt_text(stripped).strip()
        yield from self._flush_accumulator()
        if not normalized or normalized == _TURN_BOUNDARY_MARKER:
            return
        if normalized.startswith("[plain] tool:"):
            yield from super()._classify_non_json_line(stripped)
            return

        signature = _status_signature(normalized)
        tool_match = _EXECUTED_TOOL_RE.search(normalized)
        is_status = self._is_status_line(normalized)

        if tool_match is not None:
            yield AgentOutputLine(
                type="tool_use",
                content=tool_match.group("tool"),
                raw=stripped,
                metadata={"event": "nanocoder_tool_execution"},
            )
        elif not is_status and not self._text_seen(signature):
            yield AgentOutputLine(
                type="text",
                content=normalized,
                raw=stripped,
                metadata={"event": "nanocoder_text"},
            )
        elif (
            is_status
            and signature not in self._status_signatures
            and len(self._status_signatures) < _MAX_STATUS_EVENTS
        ):
            self._status_signatures = (*self._status_signatures, signature)
            yield AgentOutputLine(
                type="status",
                content=normalized,
                raw=stripped,
                metadata={"event": "interactive_tui"},
            )
