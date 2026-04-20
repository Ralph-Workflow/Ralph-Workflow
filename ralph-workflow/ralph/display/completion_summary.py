"""End-of-run completion summary rendering for log-first output."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rich.text import Text

if TYPE_CHECKING:
    from pathlib import Path

    from rich.console import Console

    from ralph.display.snapshot import DashboardSnapshot

_VERIFICATION_ARTIFACT = ".agent/artifacts/verification.json"
_DECISION_LABELS: dict[str, str] = {
    "proceed": "PASS",
    "complete": "PASS",
    "pr_opened": "INFO",
    "revise": "WARN",
    "failed": "FAIL",
}


def _read_verification_status(workspace_root: Path | None) -> tuple[str, str | None]:
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
    reason = parsed_dict.get("reason") or parsed_dict.get("summary") or parsed_dict.get("message")
    label = status if isinstance(status, str) and status else "unknown"
    reason_text = reason if isinstance(reason, str) and reason else None
    return (label, reason_text)


def _commit_sha_from_snapshot(snapshot: DashboardSnapshot) -> str | None:
    for worker in reversed(snapshot.workers):
        if worker.commit_sha:
            return worker.commit_sha
    return None


def _verification_line(
    snapshot: DashboardSnapshot,
    workspace_root: Path | None,
    *,
    failed: bool,
) -> str:
    status, reason = _read_verification_status(workspace_root)
    if status == "unknown":
        derived_ok = not failed and snapshot.last_error is None
        return "Verification: passed" if derived_ok else "Verification: not verified"
    suffix = f" — {reason}" if reason else ""
    return f"Verification: {status}{suffix}"


def render_completion_summary(
    snapshot: DashboardSnapshot,
    *,
    workspace_root: Path | None = None,
) -> Text:
    failed = snapshot.phase == "failed"
    lines: list[str] = ["Pipeline Failed" if failed else "Pipeline Complete"]

    if snapshot.plan_summary:
        lines.append(f"Plan: {snapshot.plan_summary}")
    if snapshot.plan_scope_items:
        lines.append(f"Scope: {len(snapshot.plan_scope_items)} item(s)")

    lines.append(
        "Metrics: "
        f"agent_calls={snapshot.total_agent_calls} "
        f"continuations={snapshot.total_continuations} "
        f"fallbacks={snapshot.total_fallbacks} "
        f"retries={snapshot.total_retries} "
        f"pushes={snapshot.push_count}"
    )

    if snapshot.decision_log:
        lines.append("Decisions:")
        for phase, decision, reason, _ts in snapshot.decision_log:
            badge = _DECISION_LABELS.get(decision.lower(), "INFO")
            reason_part = f" — {reason}" if reason else ""
            lines.append(f"- [{badge}] {phase.replace('_', ' ').title()}: {decision}{reason_part}")
    else:
        lines.append("Decisions: (none recorded)")

    lines.append(_verification_line(snapshot, workspace_root, failed=failed))

    sha = _commit_sha_from_snapshot(snapshot)
    if sha:
        lines.append(f"Commit: {sha[:12]}")
    if snapshot.pr_url:
        lines.append(f"PR: {snapshot.pr_url}")
    if snapshot.last_error:
        lines.append(f"Error: {snapshot.last_error}")
    if snapshot.plan_risks:
        lines.append("Open Risks:")
        lines.extend(f"- {risk}" for risk in snapshot.plan_risks)

    return Text("\n".join(lines))


def emit_completion_summary(
    console: Console,
    snapshot: DashboardSnapshot,
    *,
    workspace_root: Path | None = None,
) -> None:
    console.print(render_completion_summary(snapshot, workspace_root=workspace_root), markup=False)


__all__ = ["emit_completion_summary", "render_completion_summary"]
