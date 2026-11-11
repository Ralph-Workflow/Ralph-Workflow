
from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

from ralph.skills._agent_paths import (
    agent_skill_roots,
    project_sibling_skill_roots,
)
from ralph.skills._content import BASELINE_SKILL_NAMES

DOCS_MCP_NOT_INSTALLED_MESSAGE = (
    "Optional — configure and start a docs MCP server when you need library lookup"
)

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

    from ralph.display.context import DisplayContext
    from ralph.skills._capability_state import CapabilityState


def _row_for_root(
    *,
    agent_label: str,
    skill_root: Path,
    scope: str,
) -> tuple[str, str, str, Text]:
    resolved = skill_root
    all_present = all((resolved / name / "SKILL.md").exists() for name in BASELINE_SKILL_NAMES)
    if all_present:
        status_text = Text("OK", style="theme.status.success")
    else:
        status_text = Text("Skipped", style="theme.status.pending")
    return (agent_label, str(resolved), scope, status_text)


def collect_skill_root_rows(
    workspace_root: Path | None = None,
) -> list[tuple[str, str, str, Text]]:
    rows: list[tuple[str, str, str, Text]] = []
    for entry in agent_skill_roots():
        resolved = entry.resolve()
        label = f"{entry.agent} (canonical)" if entry.is_canonical else entry.agent
        rows.append(_row_for_root(agent_label=label, skill_root=resolved, scope="user-global"))
    if workspace_root is not None:
        for sibling in project_sibling_skill_roots(workspace_root):
            resolved = sibling.resolve(workspace_root)
            label = f"{sibling.agent} (project)"
            rows.append(_row_for_root(agent_label=label, skill_root=resolved, scope="project"))
    return rows


def print_capability_summary(
    console: Console,
    state: CapabilityState,
    *,
    workspace_root: Path | None = None,
    display_context: DisplayContext | None = None,
) -> None:
    del console
    from ralph.cli.main import _get_cli_context

    from ralph.display.parallel_display import resolve_active_display

    ctx = display_context if display_context is not None else _get_cli_context()
    display = resolve_active_display(None, ctx)
    display.emit_capability_summary(state, workspace_root=workspace_root)


__all__ = [
    "DOCS_MCP_NOT_INSTALLED_MESSAGE",
    "collect_skill_root_rows",
    "print_capability_summary",
]
