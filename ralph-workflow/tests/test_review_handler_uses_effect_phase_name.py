"""Contract test: review handler must use effect.phase (not literal 'review') for events.

The review phase handler is a generic role handler. It must not hardcode the
phase name 'review' in PhaseFailureEvent or _write_retry_hint. Instead it
must derive the phase name from the InvokeAgentEffect.phase attribute.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from ralph.phases.review import handle_review
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent


def _make_failing_context() -> MagicMock:
    """Return a PhaseContext that simulates a missing review artifact."""
    ctx = MagicMock()
    # Simulate no git repo so _current_head_sha returns None
    ctx.workspace.absolute_path.return_value = None
    # Simulate missing artifact (no review baseline, artifact load fails)
    ctx.workspace.read.side_effect = FileNotFoundError("no artifact")
    ctx.workspace.write.return_value = None
    return ctx


def test_phase_failure_event_uses_effect_phase_not_literal_review() -> None:
    """PhaseFailureEvent.phase must equal effect.phase, not the literal 'review'."""
    custom_phase_name = "custom_review_phase"
    effect = InvokeAgentEffect(
        agent_name="reviewer",
        phase=custom_phase_name,
        prompt_file="PROMPT.md",
    )
    ctx = _make_failing_context()
    with patch(
        "ralph.phases.review.load_phase_artifact",
        side_effect=ValueError("missing artifact"),
    ):
        events = handle_review(effect, ctx)

    assert len(events) == 1, f"Expected 1 event, got {events}"
    event = events[0]
    assert isinstance(event, PhaseFailureEvent), (
        f"Expected PhaseFailureEvent, got {type(event).__name__}"
    )
    assert event.phase == custom_phase_name, (
        f"PhaseFailureEvent.phase is '{event.phase}', expected '{custom_phase_name}'. "
        "The review handler still hardcodes 'review' instead of using effect.phase."
    )
    assert event.phase != "review", (
        "PhaseFailureEvent.phase is the literal 'review' — the handler is not generic."
    )
