"""Safe PROMPT.md reader with size cap, encoding safety, and markup escape."""

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
    """Return the path to the workspace prompt file, or ``None`` if absent.

    The path is resolved through
    :func:`ralph.pro_support.prompt.resolve_effective_prompt_path` so the
    ``PROMPT_PATH`` env var is honoured in Pro mode. The
    engine-owned ``.agent/CURRENT_PROMPT.md`` is still checked as a
    fallback because it is the materialised prompt the agent actually
    reads.

    The caller supplies the ``env`` mapping. Reading ``os.environ``
    directly inside the display module would violate the DI invariant
    enforced by ``tests/display/test_di_invariants.py``. Callers that
    do not have a pre-resolved env should pass
    ``context.make_display_context().env`` or fall back to
    ``os.environ`` at their own call site.
    """
    path = resolve_effective_prompt_path(workspace_root, env)
    if path.exists():
        return path
    current_prompt = workspace_root / ".agent" / "CURRENT_PROMPT.md"
    return current_prompt if current_prompt.exists() else None


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
