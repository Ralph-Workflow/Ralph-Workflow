from __future__ import annotations

from typing import Final

from loguru import logger

DEFAULT_MAX_BUFFER_CHARS: Final[int] = 1 << 20


def clamp_tail(text: str, *, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[-max_chars:]


class BoundedTextBuffer:
    __slots__ = ("_text", "_truncated_chars", "_warned", "label", "max_chars")

    def __init__(
        self,
        max_chars: int = DEFAULT_MAX_BUFFER_CHARS,
        label: str = "text",
    ) -> None:
        self.max_chars = max_chars
        self.label = label
        self._text = ""
        self._truncated_chars = 0
        self._warned = False

    def append(self, text: str) -> None:
        self.replace(self._text + text)

    @property
    def value(self) -> str:
        return self._text

    @property
    def truncated_chars(self) -> int:
        return self._truncated_chars

    def replace(self, text: str) -> None:
        retained = clamp_tail(text, max_chars=self.max_chars)
        dropped = len(text) - len(retained)
        if dropped:
            self._truncated_chars += dropped
            if not self._warned:
                logger.warning(
                    "bounded text buffer '{}' reached {} characters; dropping oldest text",
                    self.label,
                    self.max_chars,
                )
                self._warned = True
        self._text = retained

    def clear(self) -> None:
        self._text = ""
