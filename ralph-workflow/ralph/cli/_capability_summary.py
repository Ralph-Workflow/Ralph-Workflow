"""Public capability summary helpers used by `ralph --init` and `ralph --force-init-skills`.

The print side is a 2-line forwarder around
:func:`ralph.display.parallel_display.ParallelDisplay.emit_capability_summary`,
which owns the canonical implementation. The pure helper
:func:`collect_skill_root_rows` stays in this module so it remains
importable by both ``ParallelDisplay.emit_capability_summary`` and the
test suite.

The module deliberately lives at ``ralph.cli._`` (with a leading
underscore on the filename) so the underscore-prefix convention still
signals "CLI-internal helper", but the imported names are public (no
leading underscore) so the importer does not have to reach into a
private namespace.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text

from ralph.display.parallel_display import resolve_active_display
from ralph.skills._agent_paths import (
    agent_skill_roots,
    project_sibling_skill_roots,
)
from ralph.skills._content import BASELINE_SKILL_NAMES

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
    """Build the (agent, skill_root, scope, status) rows for the per-agent coverage table.

    Returns a 4-tuple per row:
      * ``agent_label`` — human-friendly agent name (e.g. ``claude``,
        ``claude (canonical)``, ``claude (project)``)
      * ``skill_root`` — the resolved absolute path string
      * ``scope`` — ``user-global`` or ``project``
      * ``status_text`` — a rich Text for the table cell

    When ``workspace_root`` is ``None`` the project-scope rows are omitted
    (callers in the user-global-only path can pass ``None``). When provided,
    three project-scope sibling rows are appended (claude, codex, agy) —
    the canonical ``./.opencode/skills/`` is intentionally absent because
    it is the fan-out source, not a sibling.
    """
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
    """Print the baseline capabilities summary table.

    Thin 2-line forwarder that delegates to
    :meth:`ParallelDisplay.emit_capability_summary`, which owns the
    canonical implementation. The ``console`` argument is accepted for
    backward compatibility and is intentionally unused — the resolved
    display owns its own console via the supplied ``DisplayContext``.
    When ``display_context`` is None the active display is built
    from the in-scope ``_cli_ctx`` singleton.
    """
    del console
    from ralph.cli.main import _get_cli_context

    ctx = display_context if display_context is not None else _get_cli_context()
    display = resolve_active_display(None, ctx)
    display.emit_capability_summary(state, workspace_root=workspace_root)


__all__ = ["collect_skill_root_rows", "print_capability_summary"]
