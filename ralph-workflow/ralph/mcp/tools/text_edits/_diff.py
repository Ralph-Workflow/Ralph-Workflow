"""Diff and digest helpers shared by the text-edit tools."""

from __future__ import annotations

import difflib
import hashlib


def sha256_text(text: str) -> str:
    """Return the SHA-256 hex digest of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unified_text_diff(original: str, current: str, *, label: str) -> str:
    """Return a unified diff between two texts, both sides labelled ``label``."""
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=label,
            tofile=label,
            lineterm="",
        )
    )
