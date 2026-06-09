"""Public capability summary helpers used by `ralph --init` and `ralph --force-init-skills`.

Both the print and the row-collector are public so they can be imported by
``ralph.cli.main`` without the private-symbol anti-pattern flagged by the
prior planning pass. The module deliberately lives at ``ralph.cli._`` (with
a leading underscore on the filename) so the underscore-prefix convention
still signals "CLI-internal helper", but the imported names are public
(no leading underscore) so the importer does not have to reach into a
private namespace.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text

from ralph.skills._agent_paths import (
    agent_skill_roots,
    project_sibling_skill_roots,
)
from ralph.skills._baseline_catalog import STATIC_BUILTIN_CAPABILITIES
from ralph.skills._content import BASELINE_SKILL_NAMES

if TYPE_CHECKING:
    from rich.console import Console

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
        rows.append(
            _row_for_root(agent_label=label, skill_root=resolved, scope="user-global")
        )
    if workspace_root is not None:
        for sibling in project_sibling_skill_roots(workspace_root):
            resolved = sibling.resolve(workspace_root)
            label = f"{sibling.agent} (project)"
            rows.append(
                _row_for_root(agent_label=label, skill_root=resolved, scope="project")
            )
    return rows


def print_capability_summary(
    console: Console, state: CapabilityState, *, workspace_root: Path | None = None
) -> None:
    """Print the baseline capabilities summary table.

    ``workspace_root`` defaults to the current working directory when None.
    The Skill root coverage table gains a new 'Scope' column when project
    rows are present; the user-global and project rows are separated by a
    blank row when both groups render.
    """
    from ralph.skills._capability_status import CapabilityStatus

    resolved_workspace = Path.cwd() if workspace_root is None else workspace_root

    table = Table(title="Baseline Capabilities", show_header=True)
    table.add_column("Capability", style="theme.cat.meta")
    table.add_column("Type")
    table.add_column("Status")
    for cap in STATIC_BUILTIN_CAPABILITIES:
        table.add_row(
            cap.name.replace("_", " ").title(),
            "Built-in",
            Text("OK — always available", style="theme.status.success"),
        )
    managed_rows = [
        ("Web search (DuckDuckGo)", state.web_search),
        ("Page retrieval (visit_url)", state.visit_url),
        ("Docs MCP (localhost:6280)", state.docs_mcp),
        ("Skill bundles", state.skills),
    ]
    for label, entry in managed_rows:
        if entry.status == CapabilityStatus.INSTALLED_HEALTHY:
            status_text = Text("OK", style="theme.status.success")
        elif entry.update_available:
            status_text = Text(
                "Update available — run `ralph --init` to update",
                style="theme.status.warning",
            )
        else:
            status_text = Text(
                f"{entry.status.value} — run `ralph --init` or check config",
                style="theme.status.warning",
            )
        table.add_row(label, "Managed", status_text)
    console.print(table)

    if state.skills.status != CapabilityStatus.NOT_INSTALLED:
        console.print(Text("Skill root coverage", style="theme.cat.meta"))
        skill_rows = collect_skill_root_rows(workspace_root=resolved_workspace)
        skill_table = Table(show_header=True)
        skill_table.add_column("Agent", style="theme.cat.meta")
        skill_table.add_column("Skill root", style="theme.text.muted")
        skill_table.add_column("Scope", style="theme.cat.meta")
        skill_table.add_column("Status")
        for agent_label, skill_root, scope, status_text in skill_rows:
            skill_table.add_row(agent_label, skill_root, scope, status_text)
        console.print(skill_table)


__all__ = ["collect_skill_root_rows", "print_capability_summary"]
