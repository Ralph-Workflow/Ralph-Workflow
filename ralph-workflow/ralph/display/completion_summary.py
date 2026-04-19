"""End-of-run completion summary rendering.

Renders a polished final summary panel that ties together what the user saw
during the live session — plan, decisions, metrics, verification, commit,
and PR URL — so the closing view mirrors the rendered state.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.console import Group
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ralph.display.panels.analysis import _DECISION_TO_SEMANTIC
from ralph.display.theme import format_status

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

    from ralph.display.snapshot import DashboardSnapshot

_VERIFICATION_ARTIFACT = ".agent/artifacts/verification.json"


def _read_verification_status(workspace_root: Path | None) -> tuple[str, str | None]:
    """Return (status_label, reason) derived from verification.json if present."""
    if workspace_root is None:
        return ("unknown", None)
    path = workspace_root / _VERIFICATION_ARTIFACT
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError, PermissionError):
        return ("unknown", None)
    try:
        parsed: object = json.loads(raw)
    except json.JSONDecodeError:
        return ("unknown", None)
    if not isinstance(parsed, dict):
        return ("unknown", None)
    parsed_dict: dict[str, object] = parsed
    status = parsed_dict.get("status") or parsed_dict.get("outcome")
    reason = (
        parsed_dict.get("reason")
        or parsed_dict.get("summary")
        or parsed_dict.get("message")
    )
    label = status if isinstance(status, str) and status else "unknown"
    reason_text = reason if isinstance(reason, str) and reason else None
    return (label, reason_text)


def _commit_sha_from_snapshot(snapshot: DashboardSnapshot) -> str | None:
    for worker in reversed(snapshot.workers):
        if worker.commit_sha:
            return worker.commit_sha
    return None


def _build_decision_table(snapshot: DashboardSnapshot) -> Table:
    table = Table(show_header=True, box=None, header_style="bold", expand=False)
    table.add_column("Phase")
    table.add_column("Decision")
    table.add_column("Reason", overflow="fold")
    for phase, decision, reason, _ts in snapshot.decision_log:
        key = decision.lower()
        semantic = _DECISION_TO_SEMANTIC.get(key, "info")
        badge = format_status(semantic)
        phase_cell = Text(phase.replace("_", " ").title())
        decision_cell = Text.from_markup(f"{badge} {escape(decision)}")
        reason_cell = Text(reason or "", style="dim")
        table.add_row(phase_cell, decision_cell, reason_cell)
    return table


def _build_metrics_line(snapshot: DashboardSnapshot) -> Text:
    parts: list[str] = [
        f"agent_calls={snapshot.total_agent_calls}",
        f"continuations={snapshot.total_continuations}",
        f"fallbacks={snapshot.total_fallbacks}",
        f"retries={snapshot.total_retries}",
        f"pushes={snapshot.push_count}",
    ]
    return Text("  ".join(parts), style="dim")


def _plan_block(snapshot: DashboardSnapshot) -> Text | None:
    if not snapshot.plan_summary and not snapshot.plan_scope_items:
        return None
    plan_text = Text()
    if snapshot.plan_summary:
        plan_text.append("Plan: ", style="bold")
        plan_text.append(snapshot.plan_summary)
    if snapshot.plan_scope_items:
        if snapshot.plan_summary:
            plan_text.append("\n")
        plan_text.append(
            f"Scope: {len(snapshot.plan_scope_items)} item(s)",
            style="dim",
        )
    return plan_text


def _verification_line(
    snapshot: DashboardSnapshot,
    workspace_root: Path | None,
    *,
    failed: bool,
) -> Text:
    status, reason = _read_verification_status(workspace_root)
    line = Text()
    line.append("Verification: ", style="bold")
    if status == "unknown":
        derived_ok = not failed and snapshot.last_error is None
        line.append(
            "passed" if derived_ok else "not verified",
            style="green" if derived_ok else "yellow",
        )
        return line
    line.append(
        status,
        style="green" if status.lower() in {"passed", "ok", "success"} else "red",
    )
    if reason:
        line.append(f" — {reason}", style="dim")
    return line


def _commit_and_pr_lines(snapshot: DashboardSnapshot) -> list[Text]:
    lines: list[Text] = []
    sha = _commit_sha_from_snapshot(snapshot)
    if sha:
        commit_line = Text()
        commit_line.append("Commit: ", style="bold")
        commit_line.append(sha[:12], style="cyan")
        lines.append(commit_line)
    if snapshot.pr_url:
        pr_line = Text()
        pr_line.append("PR: ", style="bold")
        pr_line.append(snapshot.pr_url, style="cyan")
        lines.append(pr_line)
    return lines


def _risks_block(snapshot: DashboardSnapshot) -> list[Text]:
    if not snapshot.plan_risks:
        return []
    lines: list[Text] = [Text("Open Risks:", style="bold")]
    for risk in snapshot.plan_risks:
        risk_line = Text()
        risk_line.append("  • ", style="dim")
        risk_line.append(risk)
        lines.append(risk_line)
    return lines


def render_completion_summary(
    snapshot: DashboardSnapshot,
    *,
    workspace_root: Path | None = None,
) -> Panel:
    """Render a Rich Panel summarising the run.

    The panel shows plan summary, metrics line, decision log, verification
    status (from ``.agent/artifacts/verification.json`` if present, else
    derived from the pipeline state), commit SHA from the last worker, PR
    URL when set, and open risks from the plan artifact. Title and border
    colour reflect whether the run ended in ``complete`` or ``failed``.
    """
    failed = snapshot.phase == "failed"
    title = "Pipeline Failed" if failed else "Pipeline Complete"
    border = "red" if failed else "green"

    children: list[Text | Table | Panel] = []

    plan_block = _plan_block(snapshot)
    if plan_block is not None:
        children.append(plan_block)

    children.append(_build_metrics_line(snapshot))

    if snapshot.decision_log:
        children.append(Text("Decisions", style="bold"))
        children.append(_build_decision_table(snapshot))
    else:
        children.append(Text("Decisions: (none recorded)", style="dim"))

    children.append(_verification_line(snapshot, workspace_root, failed=failed))
    children.extend(_commit_and_pr_lines(snapshot))

    if snapshot.last_error:
        err_line = Text()
        err_line.append("Error: ", style="bold red")
        err_line.append(snapshot.last_error, style="red")
        children.append(err_line)

    children.extend(_risks_block(snapshot))

    return Panel(
        Group(*children),
        title=title,
        border_style=border,
        padding=(1, 2),
    )


def emit_completion_summary(
    console: Console,
    snapshot: DashboardSnapshot,
    *,
    workspace_root: Path | None = None,
) -> None:
    """Print the completion summary panel to ``console``.

    Safe to call from both dashboard (after Live.stop()) and lines mode.
    """
    panel = render_completion_summary(snapshot, workspace_root=workspace_root)
    console.print(panel)


__all__ = ["emit_completion_summary", "render_completion_summary"]
