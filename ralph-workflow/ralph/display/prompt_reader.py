"""Safe PROMPT.md reader with size cap, encoding safety, and markup escape."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from rich.markup import escape

MAX_PROMPT_BYTES = 8192
PREVIEW_LINES = 10


def find_prompt_path(workspace_root: Path) -> Path | None:
    path = workspace_root / "PROMPT.md"
    return path if path.exists() else None


def read_prompt_preview(prompt_path: Path) -> tuple[str, ...]:
    """Read at most MAX_PROMPT_BYTES, decode with errors='replace', return PREVIEW_LINES escaped."""
    if not prompt_path.exists():
        return ("[dim]PROMPT.md not found[/dim]",)

    if not prompt_path.is_file():
        return ("[dim]PROMPT.md not a file[/dim]",)

    try:
        with prompt_path.open("rb") as f:
            data = f.read(MAX_PROMPT_BYTES)
    except PermissionError:
        return ("[dim]PROMPT.md unreadable[/dim]",)

    text = data.decode("utf-8", errors="replace")
    lines = text.split("\n")[:PREVIEW_LINES]
    return tuple(escape(line) for line in lines)
