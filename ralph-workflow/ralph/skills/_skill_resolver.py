"""Resolve explicitly requested inline skill content for prompt injection."""

from __future__ import annotations

import os
from pathlib import Path

_ENV_VAR = "RALPH_INLINE_SKILLS_DIR"


def get_inline_skill_content() -> str:
    raw_dir = os.environ.get(_ENV_VAR)
    if not raw_dir:
        return ""
    skill_dir = Path(raw_dir)
    # filesystem-read-ok: explicit inline-skill injection validates and enumerates the user-selected skill directory once
    if not skill_dir.is_dir():
        return ""

    chunks: list[str] = []
    # filesystem-read-ok: explicit inline-skill injection enumerates the user-selected directory once
    for md_file in sorted(skill_dir.glob("*.md")):
        # filesystem-read-ok: each explicitly requested inline skill is loaded once for prompt injection
        content = md_file.read_text(encoding="utf-8").strip()
        if content:
            chunks.append(content)
    return "\n\n---\n\n".join(chunks)


__all__ = ["get_inline_skill_content"]
