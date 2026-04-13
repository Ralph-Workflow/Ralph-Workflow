"""Generic NDJSON parser for other agents.

This parser handles NDJSON output from agents that don't have
a dedicated parser. It attempts to extract text content and
error information from common NDJSON formats.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.agents.parsers.base import AgentOutputLine

if TYPE_CHECKING:
    from collections.abc import Iterator


class GenericParser:
    """Generic NDJSON parser for unknown or simple agent formats.

    This parser handles NDJSON by:
    1. Parsing each line as JSON
    2. Looking for common text fields (content, text, message, output)
    3. Extracting error information
    4. Falling back to raw line storage for unparseable content
    """

    def parse(self, lines: Iterator[str]) -> Iterator[AgentOutputLine]:
        """Parse generic streaming NDJSON lines.

        Args:
            lines: Iterator of raw lines from agent stdout.

        Yields:
            Normalized AgentOutputLine instances.
        """
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            try:
                obj: dict[str, object] = json.loads(stripped)
            except json.JSONDecodeError:
                # Not JSON, treat as raw text
                yield AgentOutputLine(type="raw", content=stripped, raw=stripped)
                continue

            # Look for text content in common fields
            content = self._extract_content(obj)
            if content:
                yield AgentOutputLine(type="text", content=content, raw=stripped)

            # Check for error indicators
            if self._is_error(obj):
                error_msg = self._extract_error(obj)
                yield AgentOutputLine(
                    type="error",
                    content=error_msg,
                    raw=stripped,
                    metadata=obj,
                )
                continue

            # Check for stop/done markers
            if self._is_stop(obj):
                yield AgentOutputLine(type="stop", raw=stripped)

            # If no content was extracted but we have valid JSON, store metadata
            if not content and not self._is_stop(obj):
                yield AgentOutputLine(type="unknown", raw=stripped, metadata=obj)

    def _extract_content(self, obj: dict[str, object]) -> str:
        """Extract text content from JSON object.

        Args:
            obj: Parsed JSON object.

        Returns:
            Extracted text content or empty string.
        """
        # Check common content fields in order of preference
        for field in ("content", "text", "message", "output", "response", "result"):
            value = obj.get(field)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                # Sometimes content is nested
                nested = value.get("text") or value.get("content")
                if isinstance(nested, str) and nested:
                    return nested
        return ""

    def _is_error(self, obj: dict[str, object]) -> bool:
        """Check if object represents an error.

        Args:
            obj: Parsed JSON object.

        Returns:
            True if object appears to be an error.
        """
        type_val = str(obj.get("type", "")).lower()
        return "error" in type_val or bool(obj.get("error"))

    def _extract_error(self, obj: dict[str, object]) -> str:
        """Extract error message from object.

        Args:
            obj: Parsed JSON object.

        Returns:
            Error message string.
        """
        error = obj.get("error")
        if isinstance(error, str):
            return error
        if isinstance(error, dict):
            return str(error.get("message", error.get("type", "unknown error")))
        return str(obj.get("message", obj.get("msg", "unknown error")))

    def _is_stop(self, obj: dict[str, object]) -> bool:
        """Check if object represents end of output.

        Args:
            obj: Parsed JSON object.

        Returns:
            True if object represents end of stream.
        """
        type_val = str(obj.get("type", "")).lower()
        return type_val in ("stop", "done", "complete", "finish", "end")
