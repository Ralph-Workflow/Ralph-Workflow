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
from ralph.display.snapshot import PipelineSnapshot, WorkerSnapshot
from ralph.display.subscriber import PipelineSubscriber
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from pathlib import Path


def _make_snapshot(  # noqa: PLR0913 - test helper exposes many kwargs for coverage
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
) -> PipelineSnapshot:
    return PipelineSnapshot(
        phase=phase,
        previous_phase="development_commit",
        iteration=3,
        total_iterations=5,
        reviewer_pass=1,
        total_reviewer_passes=2,
        review_issues_found=False,
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
    text = _render_plain(_make_snapshot(phase="failed", last_error="boom", pr_url=None))
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


def test_render_verification_missing_artifact_falls_back_to_state(tmp_path: Path) -> None:
    snapshot = _make_snapshot()
    text = _render_plain(snapshot, workspace_root=tmp_path)
    assert "Verification" in text
    assert "passed" in text


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


def test_render_includes_commit_sha_from_workers() -> None:
    worker = WorkerSnapshot(
        unit_id="unit-1",
        description="first",
        status="SUCCEEDED",
        status_semantic="success",
        started_at=None,
        finished_at=None,
        elapsed_s=1.0,
        exit_code=0,
        commit_sha="abc123def456deadbeef",
        error_message=None,
    )
    text = _render_plain(_make_snapshot(workers=(worker,)))
    assert "Commit" in text
    assert "abc123def456" in text


def test_render_without_commit_sha_omits_commit_line() -> None:
    text = _render_plain(_make_snapshot())
    assert "Commit:" not in text


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
    emit_completion_summary(console, _make_snapshot())
    out = console.export_text()
    assert "Pipeline Complete" in out


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

    text = _render_plain(snapshot, workspace_root=tmp_path)
    assert "Pipeline Complete" in text
    assert "Development Analysis" in text
    assert "proceed" in text
    assert "all green" in text
    # phase transition row from record_phase_transition
    assert "→ development" in text
