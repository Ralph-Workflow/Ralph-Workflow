from __future__ import annotations

import difflib
import hashlib


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def unified_text_diff(original: str, current: str, *, label: str) -> str:
    return "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            current.splitlines(keepends=True),
            fromfile=label,
            tofile=label,
            lineterm="",
        )
    )
