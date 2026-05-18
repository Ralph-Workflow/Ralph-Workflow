"""Immutable pipeline state snapshot for transcript rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from ralph.display.budget_progress import BudgetProgress
    from ralph.display.worker_snapshot import WorkerSnapshot


@dataclass(frozen=True, slots=True)
class PipelineSnapshot:
    """Immutable pipeline state snapshot for transcript rendering."""

    phase: str
    previous_phase: str | None
    review_issues_found: bool
    interrupted_by_user: bool
    last_error: str | None
    pr_url: str | None
    push_count: int
    total_agent_calls: int
    total_continuations: int
    total_fallbacks: int
    total_retries: int
    workers: tuple[WorkerSnapshot, ...]
    prompt_path: str | None
    prompt_preview: tuple[str, ...]
    run_id: str | None
    created_at: datetime
    plan_summary: str | None = None
    plan_scope_items: tuple[str, ...] = ()
    plan_total_steps: int = 0
    plan_current_step: int | None = None
    plan_risks: tuple[str, ...] = ()
    active_agent: str | None = None
    active_tool: str | None = None
    active_path: str | None = None
    active_unit_id: str | None = None
    active_workdir: str | None = None
    active_command: str | None = None
    active_pattern: str | None = None
    last_activity_line: str | None = None
    waiting_status_line: str | None = None
    analysis_phase: str | None = None
    analysis_decision: str | None = None
    analysis_reason: str | None = None
    decision_log: tuple[tuple[str, str, str, str], ...] = field(default_factory=tuple)
    recovery_cycle_count: int = 0
    recovery_cycle_cap: int = 200
    fallover_history: tuple[tuple[str, str, str, str], ...] = field(default_factory=tuple)
    last_failure_category: str | None = None
    last_connectivity_state: str = "unknown"
    is_terminal_success: bool = False
    is_terminal_failure: bool = False
    current_phase_role: str | None = None
    previous_phase_role: str | None = None
    terminal_failure_route: str | None = None
    budget_progress: dict[str, BudgetProgress] = field(default_factory=dict)
    outer_dev_iteration: int | None = None
    mcp_restart_count: int = 0
    active_process_labels: tuple[str, ...] = ()
