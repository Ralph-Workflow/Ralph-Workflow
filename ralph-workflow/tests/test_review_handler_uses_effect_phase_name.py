"""Contract test: review handler must use effect.phase (not literal 'review') for events.

The review phase handler is a generic role handler. It must not hardcode the
phase name 'review' in PhaseFailureEvent or _write_retry_hint. Instead it
must derive the phase name from the InvokeAgentEffect.phase attribute.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

from ralph.phases import PhaseContext
from ralph.phases.review import handle_review
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent


@dataclass(slots=True)
class _FakeWorkspace:
    def absolute_path(self, rel: str) -> None:
        del rel

    def read(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        raise FileNotFoundError("no artifact")

    def write(self, *args: object, **kwargs: object) -> None:
        del args, kwargs


def _make_failing_context() -> PhaseContext:
    """Return a PhaseContext that simulates a missing review artifact."""
    workspace: Any = _FakeWorkspace()
    registry: Any = object()
    chain_manager: Any = object()
    pipeline_policy: Any = object()
    agents_policy: Any = object()
    artifacts_policy: Any = object()
    return PhaseContext.model_construct(
        workspace=workspace,
        registry=registry,
        chain_manager=chain_manager,
        pipeline_policy=pipeline_policy,
        agents_policy=agents_policy,
        artifacts_policy=artifacts_policy,
    )


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
