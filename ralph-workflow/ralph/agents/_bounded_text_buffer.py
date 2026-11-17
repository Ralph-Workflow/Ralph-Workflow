from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

from loguru import logger

DEFAULT_MAX_BUFFER_CHARS: Final[int] = 1 << 20


def clamp_tail(text: str, *, max_chars: int) -> str:
    return text if len(text) <= max_chars else text[-max_chars:]


@dataclass(slots=True)
class BoundedTextBuffer:
    max_chars: int = DEFAULT_MAX_BUFFER_CHARS
    label: str = "text"
    _text: str = field(default="", init=False, repr=False)
    _truncated_chars: int = field(default=0, init=False, repr=False)
    _warned: bool = field(default=False, init=False, repr=False)

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
