"""Regression coverage for conflict-footer watchdog attention ownership (S-1)."""

from __future__ import annotations

from pathlib import Path

from ralph.display.status_bar import StatusBarModel
from ralph.pipeline.conflict_resolution.status import push_conflict_status_bar


class _Display:
    def __init__(self) -> None:
        self.models: list[StatusBarModel] = []

    def update_status_bar(self, model: object) -> None:
        assert isinstance(model, StatusBarModel)
        self.models.append(model)


def test_conflict_footer_regression_keeps_watchdog_attention_live() -> None:
    """S-1: conflict pushes leave attention unset for Live-tick substitution."""
    display = _Display()

    push_conflict_status_bar(
        display,
        Path("/workspace"),
        target="main",
        round_index=1,
        round_cap=3,
    )

    assert display.models[-1].attention is None


def test_push_conflict_status_bar_evidence_diagnosis_never_shown_as_conflict() -> None:
    """Evidence diagnosis must never be shown or counted as conflict resolution."""
    display = _Display()

    push_conflict_status_bar(
        display,
        Path("/workspace"),
        target="main",
        round_index=1,
        round_cap=3,
        stop_index=1,
        stop_cap=2,
        is_diagnosis=True,
    )

    last_model = display.models[-1]
    assert last_model.phase_label.startswith("Evidence Diagnosis")
    assert "conflict" not in last_model.phase_label.lower()
    assert last_model.outer_label == "Diagnosis"
    assert last_model.operation_display_state == "evidence_diagnosis"


def test_push_conflict_status_bar_conflict_resolution_label() -> None:
    """Conflict resolution pushes retain standard phase label and display state."""
    display = _Display()

    push_conflict_status_bar(
        display,
        Path("/workspace"),
        target="main",
        round_index=1,
        round_cap=3,
        stop_index=1,
        stop_cap=2,
    )

    last_model = display.models[-1]
    assert last_model.phase_label.startswith("Rebase Conflict Resolution")
    assert "evidence" not in last_model.phase_label.lower()
    assert last_model.outer_label == "Round"
    assert last_model.operation_display_state == "conflict_resolution"


def test_resolution_status_reporter_distinguishes_evidence_diagnosis() -> None:
    """ResolutionStatusReporter emits 'evidence diagnosis status' on diagnosis channel."""
    from ralph.pipeline.conflict_resolution.status import ResolutionStatusReporter

    class _WarnDisplay:
        def __init__(self) -> None:
            self.lines: list[tuple[str, str, str]] = []

        def emit_warn_line(self, target: str, channel: str, message: str) -> None:
            self.lines.append((target, channel, message))

    class _FakeEvent:
        def __init__(self) -> None:
            self.diagnostic = {"last_activity_kind": "tool_call", "last_activity_age_seconds": 1.0}

    # Diagnosis reporter
    disp_diag = _WarnDisplay()
    rep_diag = ResolutionStatusReporter(
        display=disp_diag,
        target="main",
        round_index=1,
        round_cap=3,
        stop_index=None,
        stop_cap=None,
        clock=lambda: 10.0,
        interval_seconds=1.0,
        started_at=5.0,
        unresolved_paths=("file.py",),
        is_diagnosis=True,
    )
    rep_diag.observe(_FakeEvent())
    assert len(disp_diag.lines) == 1
    _target, channel, message = disp_diag.lines[0]
    assert channel == "evidence-diagnosis"
    assert message.startswith("evidence diagnosis status: ")
    assert "conflict resolution" not in message

    # Conflict reporter
    disp_conf = _WarnDisplay()
    rep_conf = ResolutionStatusReporter(
        display=disp_conf,
        target="main",
        round_index=1,
        round_cap=3,
        stop_index=None,
        stop_cap=None,
        clock=lambda: 10.0,
        interval_seconds=1.0,
        started_at=5.0,
        unresolved_paths=("file.py",),
        is_diagnosis=False,
    )
    rep_conf.observe(_FakeEvent())
    assert len(disp_conf.lines) == 1
    _target2, channel2, message2 = disp_conf.lines[0]
    assert channel2 == "rebase-conflict"
    assert message.startswith("evidence diagnosis status: ")
    assert message2.startswith("conflict resolution status: ")
    assert "evidence diagnosis" not in message2
