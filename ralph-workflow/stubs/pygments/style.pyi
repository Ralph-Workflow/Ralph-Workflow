"""Minimal Pygments style surface used by Ralph's syntax themes."""

from typing import ClassVar

class Style:
    default_style: ClassVar[str]
    styles: ClassVar[dict[object, str]]
