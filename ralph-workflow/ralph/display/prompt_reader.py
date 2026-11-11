
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

from rich.markup import escape

from ralph.pro_support.prompt import resolve_effective_prompt_path

MAX_PROMPT_BYTES = 8192
PREVIEW_LINES = 10


def find_prompt_path(
    workspace_root: Path,
    env: Mapping[str, str] | None = None,
) -> Path | None:
    path = resolve_effective_prompt_path(workspace_root, env)
    if path.exists():
        return path
    product_criteria = workspace_root / ".agent" / "PRODUCT_CRITERIA.md"
    return product_criteria if product_criteria.exists() else None


def read_prompt_preview(prompt_path: Path) -> tuple[str, ...]:
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
