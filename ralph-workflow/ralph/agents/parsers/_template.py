"""Template-method base class for agent output parsers.

Provides the shared ``parse`` template, ``parse_json_line`` for standard
JSON-vs-raw dispatch, ``is_stop_event`` for stop-type checking, and
``flush_accumulators`` for draining pending text accumulators.

Subclasses override ``classify_line`` for parser-specific line dispatch
and optionally ``flush_accumulators`` when the accumulator layout differs
from the standard dict-based pattern.
"""

from __future__ import annotations

import json
from json import JSONDecodeError
from typing import TYPE_CHECKING, ClassVar, cast

from .agent_output_line import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator

    from .text_accumulator import TextAccumulator

__all__ = ["ParserTemplateBase"]


class ParserTemplateBase:
    """Template-method base for agent output parsers.

    Class attributes (override in subclasses):
        _STOP_EVENT_TYPES: Known stop-event type names.

    Instance attributes (used by the default implementation):
        _accumulators: dict mapping keys to TextAccumulator instances.
    """

    _STOP_EVENT_TYPES: ClassVar[frozenset[str]] = frozenset()

    _accumulators: dict[str, TextAccumulator]

    def __init__(self) -> None:
        self._accumulators = {}

    def parse_json_line(self, line: str) -> AgentOutputLine | None:
        """Try to parse *line* as JSON and classify the result.

        Returns:
            * An ``AgentOutputLine(type='raw', ...)`` when the line is not valid
              JSON or is a JSON non-dict value (e.g. a bare string or number).
            * The return value of ``self._classify_json_object(...)`` when the
              line is a valid JSON dict.
            * ``None`` when ``_classify_json_object`` returns ``None``.
        """
        stripped = line.strip()
        try:
            parsed: object = json.loads(stripped, strict=False)
        except JSONDecodeError:
            return AgentOutputLine(type="raw", content=stripped, raw=line)
        if not isinstance(parsed, dict):
            return AgentOutputLine(type="raw", content=stripped, raw=line)
        return self._classify_json_object(cast("dict[str, object]", parsed), line)

    def _classify_json_object(
        self,
        parsed: dict[str, object],
        raw: str,
    ) -> AgentOutputLine | None:
        """Subclass hook: classify a successfully parsed JSON dict.

        The default implementation returns ``None``, signalling that the JSON
        object was not handled by the template's simple path.  Subclasses may
        return an ``AgentOutputLine`` or ``None``.
        """
        return None

    def is_stop_event(self, event_type: str) -> bool:
        """Return True if *event_type* is a known stop marker."""
        return event_type in self._STOP_EVENT_TYPES

    def flush_accumulators(self) -> Iterator[AgentOutputLine]:
        """Flush all pending text accumulators and yield their content."""
        for key in list(self._accumulators.keys()):
            acc = self._accumulators.pop(key)
            yield from acc.flush(kind="text")

    def classify_line(self, line: str) -> Iterator[AgentOutputLine]:
        """Subclass hook: classify a single raw line and yield output lines.

        This is the primary extension point for parser-specific line dispatch.
        The default implementation calls ``parse_json_line`` and yields the
        result if non-``None``.
        """
        result = self.parse_json_line(line)
        if result is not None:
            yield result

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse all *lines* using the template method.

        Iterates each line through ``classify_line``, then drains any pending
        accumulators via ``flush_accumulators``.
        """
        for line in lines:
            yield from self.classify_line(line)
        yield from self.flush_accumulators()
