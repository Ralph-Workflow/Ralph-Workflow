"""Tests for the end-of-run completion summary renderer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from queue import Queue
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.display.completion_summary import (
    emit_completion_summary,
    render_completion_summary,
)
from ralph.display.context import make_display_context
from ralph.display.snapshot import BudgetProgress, PipelineSnapshot, WorkerSnapshot
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from pathlib import Path


def _make_snapshot(  # noqa: PLR0913
    *,
    phase: str = "complete",
    plan_summary: str | None = "Implement the feature",
    plan_scope_items: tuple[str, ...] = ("Item A", "Item B"),
    decision_log: tuple[tuple[str, str, str, str], ...] = (
        (
            "development_analysis",
            "proceed",
            "tests green",
            "2026-04-18T12:00:00+00:00",
        ),
        ("review_analysis", "revise", "nit fixes", "2026-04-18T12:05:00+00:00"),
    ),
    pr_url: str | None = "https://example.com/pr/42",
    last_error: str | None = None,
    workers: tuple[WorkerSnapshot, ...] = (),
    plan_risks: tuple[str, ...] = (),
    is_terminal_success: bool = True,
    is_terminal_failure: bool = False,
    review_issues_found: bool = False,
) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase=phase,
        previous_phase="development_commit",
        review_issues_found=review_issues_found,
        interrupted_by_user=False,
        last_error=last_error,
        pr_url=pr_url,
        push_count=2,
        total_agent_calls=7,
        total_continuations=2,
        total_fallbacks=1,
        total_retries=0,
        workers=workers,
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="run-1",
        created_at=datetime(2026, 4, 18, 12, 10, tzinfo=UTC),
        plan_summary=plan_summary,
        plan_scope_items=plan_scope_items,
        plan_total_steps=3,
        plan_current_step=3,
        plan_risks=plan_risks,
        decision_log=decision_log,
        is_terminal_success=is_terminal_success,
        is_terminal_failure=is_terminal_failure,
    )


def _render_plain(snapshot: PipelineSnapshot, *, workspace_root: Path | None = None) -> str:
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    console.print(render_completion_summary(snapshot, workspace_root=workspace_root))
    return console.export_text()


def test_render_success_title_and_plan_summary() -> None:
    text = _render_plain(_make_snapshot())
    assert "Pipeline Complete" in text
    assert "Implement the feature" in text
    assert "Scope: 2 item(s)" in text


def test_render_failure_uses_failed_title() -> None:
    text = _render_plain(
        _make_snapshot(
            phase="failed",
            last_error="boom",
            pr_url=None,
            is_terminal_success=False,
            is_terminal_failure=True,
        )
    )
    assert "Pipeline Failed" in text
    assert "boom" in text


def test_render_decision_log_renders_all_rows() -> None:
    text = _render_plain(_make_snapshot())
    assert "development_analysis".replace("_", " ").title() in text
    assert "review_analysis".replace("_", " ").title() in text
    assert "proceed" in text
    assert "revise" in text


def test_render_without_decision_log_shows_none_line() -> None:
    text = _render_plain(_make_snapshot(decision_log=()))
    assert "none recorded" in text


def test_render_metrics_line_included() -> None:
    text = _render_plain(_make_snapshot())
    assert "agent_calls=7" in text
    assert "pushes=2" in text


def test_render_verification_missing_artifact_shows_not_verified(tmp_path: Path) -> None:
    snapshot = _make_snapshot()
    text = _render_plain(snapshot, workspace_root=tmp_path)
    assert "Verification" in text
    assert "not verified" in text


def test_render_verification_missing_artifact_never_claims_passed(tmp_path: Path) -> None:
    # A missing verification artifact must not report 'passed' — the pipeline's
    # own phase/error state is not a substitute for actual verification evidence.
    snapshot = _make_snapshot(phase="complete", last_error=None)
    text = _render_plain(snapshot, workspace_root=tmp_path)
    assert "Verification: passed" not in text


def test_render_verification_reads_artifact_when_present(tmp_path: Path) -> None:
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "verification.json").write_text(
        json.dumps({"status": "failed", "reason": "lint errors"})
    )
    text = _render_plain(_make_snapshot(), workspace_root=tmp_path)
    assert "Verification" in text
    assert "failed" in text
    assert "lint errors" in text


def test_render_verification_reads_wrapped_artifact_content(tmp_path: Path) -> None:
    artifacts = tmp_path / ".agent" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "verification.json").write_text(
        json.dumps(
            {
                "name": "verification",
                "type": "verification",
                "content": {
                    "status": "failed",
                    "reason": "wrapped lint errors",
                },
            }
        ),
        encoding="utf-8",
    )
    text = _render_plain(_make_snapshot(), workspace_root=tmp_path)
    assert "Verification" in text
    assert "failed" in text
    assert "wrapped lint errors" in text


def test_render_includes_commit_message_artifact_when_present(tmp_path: Path) -> None:
    commit_dir = tmp_path / ".agent" / "tmp"
    commit_dir.mkdir(parents=True)
    (commit_dir / "commit_message.json").write_text(
        json.dumps(
            {
                "name": "commit_message",
                "type": "commit_message",
                "content": {
                    "type": "commit",
                    "subject": "feat(display): surface polished completion output",
                    "body_summary": "Show the final commit message in the completion summary.",
                },
                "created_at": "STATIC",
                "updated_at": "STATIC",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    text = _render_plain(_make_snapshot(), workspace_root=tmp_path)
    assert "Commit Message" in text
    assert "feat(display): surface polished completion output" in text
    assert "Show the final commit message in the completion summary." in text


def test_render_pr_url_included_when_set() -> None:
    text = _render_plain(_make_snapshot())
    assert "https://example.com/pr/42" in text


def test_render_missing_pr_url_omits_pr_line() -> None:
    text = _render_plain(_make_snapshot(pr_url=None))
    assert "PR:" not in text


def test_render_without_commit_message_artifact_omits_commit_message_line(tmp_path: Path) -> None:
    text = _render_plain(_make_snapshot(), workspace_root=tmp_path)
    assert "Commit Message:" not in text


def test_render_risks_section_lists_items() -> None:
    text = _render_plain(_make_snapshot(plan_risks=("risk one", "risk two")))
    assert "Open Risks" in text
    assert "risk one" in text
    assert "risk two" in text


def test_emit_completion_summary_writes_to_console() -> None:
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console)
    emit_completion_summary(_make_snapshot(), display_context=ctx)
    out = console.export_text()
    assert "Pipeline Complete" in out


def test_completion_summary_surfaces_children_persist_diagnostic() -> None:
    """CHILDREN_PERSIST_TOO_LONG last_error produces a parsed Reason line."""
    error = (
        "Agent kept child agents alive without producing output for 1800s"
        " (cumulative=1800.0s, scoped_child_active=True, oldest_child_seconds=720.0s,"
        " workspace_event_delta=0, lifecycle_only_activity=True)"
    )
    snap = _make_snapshot(phase="failed", last_error=error, pr_url=None)
    text = _render_plain(snap)
    # Key parts of the original error are present (console may wrap long lines)
    assert "kept child agents alive" in text
    # Parsed reason line is also present
    assert "Reason: long child wait" in text
    assert "cumulative=1800.0s" in text
    assert "scoped_child_active=True" in text


def test_completion_summary_unchanged_for_other_errors() -> None:
    """Unrelated errors do not produce an extra Reason line."""
    snap = _make_snapshot(phase="failed", last_error="Some other failure", pr_url=None)
    text = _render_plain(snap)
    assert "Some other failure" in text
    assert "Reason: long child wait" not in text


def test_render_review_issues_found_surfaced_in_summary() -> None:
    """When review found issues, the completion summary must surface review_issues_found."""
    snap = _make_snapshot(review_issues_found=True)
    text = _render_plain(snap)
    # The completion summary should explicitly surface the review outcome
    assert "review_issues_found" in text.lower() or "issues found" in text.lower(), (
        f"Expected review_issues_found to be surfaced in output, got: {text}"
    )


def test_render_review_clean_when_no_issues() -> None:
    """When review found no issues, the completion summary should indicate clean review."""
    snap = _make_snapshot(review_issues_found=False)
    text = _render_plain(snap)
    # The completion summary should indicate clean review when issues_found is False
    assert "review" in text.lower()
    # When issues_found is False, the summary should not claim issues were found
    assert "issues found" not in text.lower()


def test_emit_completion_summary_uses_subscriber_decision_log(tmp_path: Path) -> None:
    """Snapshot built via subscriber.build_snapshot drives the completion panel."""
    queue: Queue = Queue(maxsize=64)
    subscriber = PipelineSubscriber(
        queue=queue,
        workspace_root=tmp_path,
        run_id="r1",
    )
    subscriber.record_phase_transition("planning", "development")
    subscriber.record_analysis("development_analysis", "proceed", "all green")

    state = PipelineState(phase="complete", previous_phase="review_commit")
    snapshot = subscriber.build_snapshot(state)
    assert snapshot is not None

    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console)
    emit_completion_summary(snapshot, display_context=ctx, workspace_root=tmp_path)
    out = console.export_text()
    assert "Pipeline Complete" in out
    assert "Development Analysis" in out
    assert "proceed" in out
    assert "all green" in out
    # phase transition row from record_phase_transition
    assert "\u2192 development" in out


def test_completion_summary_outer_dev_uses_canonical_label() -> None:
    """outer_dev_iteration in completion summary uses 'Dev #N' canonical label."""
    snap = PipelineSnapshot(
        phase="fix",
        previous_phase="development",
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=3,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="run-2",
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        outer_dev_iteration=4,
    )
    text = _render_plain(snap)
    assert "Dev #4" in text
    assert "Outer Dev Iteration:" not in text


def test_completion_summary_outer_dev_with_cap_shows_n_of_total() -> None:
    """Dev N/cap format used when budget_progress has a tracks_budget counter with cap."""
    snap = PipelineSnapshot(
        phase="fix",
        previous_phase="development",
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=2,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="run-cap",
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        outer_dev_iteration=2,
        budget_progress={
            "iteration": BudgetProgress(
                description="Outer dev", completed=2, cap=5, tracks_budget=True
            )
        },
    )
    text = _render_plain(snap)
    assert "Dev 2/5" in text
    assert "Dev #2" not in text


def test_completion_summary_elapsed_appears_before_metrics() -> None:
    """Elapsed line appears before the Metrics line in text mode output."""
    snap = _make_snapshot()
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    rendered = render_completion_summary(snap, elapsed_seconds=30.0)
    console.print(rendered)
    text = console.export_text()
    assert "Elapsed: 30.0s" in text
    assert text.index("Elapsed:") < text.index("Metrics:")


def test_completion_summary_iteration_context_label_shown_for_outer_dev() -> None:
    """Text mode shows 'Iteration Context:' section heading when outer_dev_iteration is set."""
    snap = PipelineSnapshot(
        phase="complete",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=1,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="run-x",
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        outer_dev_iteration=3,
    )
    text = _render_plain(snap)
    assert "Iteration Context:" in text
    assert "Dev #3" in text


def test_completion_summary_no_iteration_context_label_when_absent() -> None:
    """Text mode omits 'Iteration Context:' section when no iteration fields are set."""
    snap = _make_snapshot()
    text = _render_plain(snap)
    assert "Iteration Context:" not in text


# --- Budget Progress tests ---

def _make_snapshot_with_budget(budget_progress: dict) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase="complete",
        previous_phase="development",
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=5,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="run-bp",
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        budget_progress=budget_progress,
    )


def test_completion_summary_budget_progress_never_shown() -> None:
    """Text mode never shows 'Budget Progress:' or 'remaining' budget wording."""
    snap = _make_snapshot_with_budget({
        "dev_cycles": BudgetProgress(
            completed=3, cap=10, description="Dev Cycles", tracks_budget=True
        ),
    })
    text = _render_plain(snap)
    assert "Budget Progress:" not in text
    assert "remaining" not in text
    assert "BUDGET:" not in text


def test_completion_summary_budget_progress_absent_when_no_tracked_budget() -> None:
    """Text mode omits budget wording when no budget-tracked counters exist."""
    snap = _make_snapshot_with_budget({
        "dev_cycles": BudgetProgress(
            completed=3, cap=10, description="Dev Cycles", tracks_budget=False
        ),
    })
    text = _render_plain(snap)
    assert "Budget Progress:" not in text


def test_completion_summary_budget_progress_absent_when_no_budget_progress() -> None:
    """Text mode omits budget wording when budget_progress dict is empty."""
    snap = _make_snapshot_with_budget({})
    text = _render_plain(snap)
    assert "Budget Progress:" not in text


def test_completion_summary_budget_progress_absent_when_cap_zero() -> None:
    """Text mode omits budget wording when cap is 0 (uncapped counter)."""
    snap = _make_snapshot_with_budget({
        "dev_cycles": BudgetProgress(
            completed=3, cap=0, description="Dev Cycles", tracks_budget=True
        ),
    })
    text = _render_plain(snap)
    assert "Budget Progress:" not in text


# --- Exit trigger tests ---


def test_completion_summary_exit_trigger_completed_for_success() -> None:
    """Text mode shows 'Exit: completed' when is_terminal_success=True."""
    snap = _make_snapshot(is_terminal_success=True, is_terminal_failure=False)
    text = _render_plain(snap)
    assert "Exit: completed" in text


def test_completion_summary_exit_trigger_failed_for_failure() -> None:
    """Text mode shows 'Exit: failed' when is_terminal_failure=True."""
    snap = _make_snapshot(
        phase="failed",
        last_error="boom",
        pr_url=None,
        is_terminal_success=False,
        is_terminal_failure=True,
    )
    text = _render_plain(snap)
    assert "Exit: failed" in text


def test_completion_summary_exit_trigger_interrupted() -> None:
    """Text mode shows 'Exit: interrupted' when interrupted_by_user=True."""
    snap = PipelineSnapshot(
        phase="complete",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=True,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=1,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="run-x",
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        is_terminal_success=False,
        is_terminal_failure=False,
    )
    text = _render_plain(snap)
    assert "Exit: interrupted" in text


def test_completion_summary_exit_trigger_appears_before_elapsed() -> None:
    """Exit: line appears before Elapsed: line in text mode."""
    snap = _make_snapshot()
    rendered = render_completion_summary(snap, elapsed_seconds=20.0)
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    console.print(rendered)
    text = console.export_text()
    assert "Exit:" in text
    assert "Elapsed:" in text
    assert text.index("Exit:") < text.index("Elapsed:")


# --- Debug breadcrumbs tests ---


def _make_snapshot_with_debug(
    last_activity_line: str | None = None,
    waiting_status_line: str | None = None,
    last_failure_category: str | None = None,
) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase="complete",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=1,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="run-debug",
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        last_activity_line=last_activity_line,
        waiting_status_line=waiting_status_line,
        last_failure_category=last_failure_category,
    )


def test_completion_summary_debug_section_shows_last_activity() -> None:
    """Debug section surfaces last_activity_line when set."""
    snap = _make_snapshot_with_debug(last_activity_line="read file: src/main.py")
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console)
    emit_completion_summary(snap, display_context=ctx)
    out = console.export_text()
    assert "last_activity: read file: src/main.py" in out


def test_completion_summary_debug_section_shows_waiting_status() -> None:
    """Debug section surfaces waiting_status_line when set."""
    snap = _make_snapshot_with_debug(waiting_status_line="waiting for MCP response")
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console)
    emit_completion_summary(snap, display_context=ctx)
    out = console.export_text()
    assert "waiting: waiting for MCP response" in out


def test_completion_summary_debug_section_shows_failure_category() -> None:
    """Debug section surfaces last_failure_category when set."""
    snap = _make_snapshot_with_debug(last_failure_category="timeout")
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console)
    emit_completion_summary(snap, display_context=ctx)
    out = console.export_text()
    assert "failure_category: timeout" in out


def test_completion_summary_debug_section_absent_when_no_debug_fields() -> None:
    """Debug section is absent when no debug fields are set on the snapshot."""
    snap = _make_snapshot()
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console)
    emit_completion_summary(snap, display_context=ctx)
    out = console.export_text()
    assert "last_activity:" not in out
    assert "waiting:" not in out
    assert "failure_category:" not in out


def test_completion_summary_debug_section_shows_mcp_restart_count() -> None:
    """Debug section surfaces mcp_restart_count when non-zero."""
    snap = PipelineSnapshot(
        phase="complete",
        previous_phase=None,
        review_issues_found=False,
        interrupted_by_user=False,
        last_error=None,
        pr_url=None,
        push_count=0,
        total_agent_calls=1,
        total_continuations=0,
        total_fallbacks=0,
        total_retries=0,
        workers=(),
        prompt_path="PROMPT.md",
        prompt_preview=(),
        run_id="run-mcp",
        created_at=datetime(2026, 4, 18, 12, 0, tzinfo=UTC),
        mcp_restart_count=2,
    )
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console)
    emit_completion_summary(snap, display_context=ctx)
    out = console.export_text()
    assert "mcp_restarts: 2" in out


def test_completion_summary_debug_section_omits_mcp_restart_count_when_zero() -> None:
    """Debug section omits mcp_restarts line when count is zero."""
    snap = _make_snapshot_with_debug()
    console = Console(record=True, width=120, force_terminal=False, color_system=None)
    ctx = make_display_context(console=console)
    emit_completion_summary(snap, display_context=ctx)
    out = console.export_text()
    assert "mcp_restarts:" not in out
