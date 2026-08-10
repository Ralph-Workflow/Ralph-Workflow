"""Tests for the S-5 transport-neutral capability-observation seam.

The recorder is the single source of truth for "what the display
layer actually rendered" during a smoke run. An empty
``observed_capabilities`` set means the shared preview builder
never produced a renderable -- the smoking gun for the original
OpenCode defect. These tests pin the recorder's contract (bounded
storage, transport-neutral observation, capability mapping).
"""

from __future__ import annotations

import pytest

from ralph.agents.display_capabilities import (
    DisplayCapability,
    all_display_capabilities,
)
from ralph.agents.display_capability_stance import DisplayCapabilityStance
from ralph.display.capability_observation import CapabilityObservation
from ralph.display.capability_observation_recorder import (
    CapabilityObservationRecorder,
    capability_for_render,
    infer_surface_for_preview,
)
from ralph.display.preview_payload import PreviewOperation, PreviewPayload


def test_recorder_starts_empty() -> None:
    recorder = CapabilityObservationRecorder()
    assert recorder.observed_capabilities() == frozenset()
    assert recorder.observed_count() == 0


def test_recorder_records_an_observation() -> None:
    recorder = CapabilityObservationRecorder()
    recorder.record(
        CapabilityObservation(
            capability=DisplayCapability.SYNTAX_HIGHLIGHTING,
            tool_name="write",
            unit_id="u-1",
        )
    )
    assert recorder.observed_capabilities() == frozenset({DisplayCapability.SYNTAX_HIGHLIGHTING})
    assert recorder.observed_count() == 1


def test_recorder_deduplicates_per_capability() -> None:
    """The set of observed capabilities is per-capability, not per-event."""
    recorder = CapabilityObservationRecorder()
    for _ in range(3):
        recorder.record(
            CapabilityObservation(
                capability=DisplayCapability.SYNTAX_HIGHLIGHTING,
                tool_name="write",
                unit_id="u-1",
            )
        )
    assert recorder.observed_capabilities() == frozenset({DisplayCapability.SYNTAX_HIGHLIGHTING})
    assert recorder.observed_count() == 3


def test_recorder_observations_for_capability_returns_arrival_order() -> None:
    recorder = CapabilityObservationRecorder()
    recorder.record(
        CapabilityObservation(
            capability=DisplayCapability.EDIT_DIFF, tool_name="edit", unit_id="u-1"
        )
    )
    recorder.record(
        CapabilityObservation(
            capability=DisplayCapability.EDIT_DIFF, tool_name="edit-2", unit_id="u-1"
        )
    )
    obs = recorder.observations_for_capability(DisplayCapability.EDIT_DIFF)
    assert [o.tool_name for o in obs] == ["edit", "edit-2"]


def test_recorder_clear_drops_all_observations() -> None:
    recorder = CapabilityObservationRecorder()
    recorder.record(
        CapabilityObservation(
            capability=DisplayCapability.SYNTAX_HIGHLIGHTING,
            tool_name="write",
            unit_id="u-1",
        )
    )
    recorder.clear()
    assert recorder.observed_capabilities() == frozenset()
    assert recorder.observed_count() == 0


def test_recorder_iter_yields_observations() -> None:
    recorder = CapabilityObservationRecorder()
    for capability in all_display_capabilities():
        recorder.record(
            CapabilityObservation(capability=capability, tool_name="t", unit_id="u")
        )
    assert [o.capability for o in recorder] == list(all_display_capabilities())


# ---------------------------------------------------------------------------
# capability_for_render: surface name to capability mapping
# ---------------------------------------------------------------------------


def test_capability_for_render_maps_syntax_preview() -> None:
    assert capability_for_render(surface_name="syntax_preview", tool_name="write") is (
        DisplayCapability.SYNTAX_HIGHLIGHTING
    )


def test_capability_for_render_maps_file_preview() -> None:
    assert capability_for_render(surface_name="file_preview", tool_name="read") is (
        DisplayCapability.FILE_PREVIEW
    )


def test_capability_for_render_maps_diff_preview() -> None:
    assert capability_for_render(surface_name="diff_preview", tool_name="edit") is (
        DisplayCapability.EDIT_DIFF
    )


def test_capability_for_render_returns_none_for_unknown_surface() -> None:
    assert capability_for_render(surface_name="welcome", tool_name=None) is None
    assert capability_for_render(surface_name="not_a_surface", tool_name="t") is None


def test_capability_for_render_accepts_none_tool_name() -> None:
    """The tool name is informational; a None tool name still maps the surface."""
    assert capability_for_render(surface_name="syntax_preview", tool_name=None) is (
        DisplayCapability.SYNTAX_HIGHLIGHTING
    )


# ---------------------------------------------------------------------------
# infer_surface_for_preview: operation to catalog surface
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("operation", "expected_surface"),
    [
        ("read", "file_preview"),
        ("write", "syntax_preview"),
        ("append", "syntax_preview"),
        ("NotebookEdit", "syntax_preview"),
        ("replace", "diff_preview"),
        ("patch", "diff_preview"),
    ],
)
def test_infer_surface_for_preview_routes_all_known_operations(
    operation: PreviewOperation, expected_surface: str
) -> None:
    """Every PreviewOperation maps to the correct catalog surface."""
    assert infer_surface_for_preview(renderable=None, canonical_operation=operation) == expected_surface


def test_infer_surface_for_preview_defaults_to_syntax_preview() -> None:
    """Unknown operations fall back to ``syntax_preview`` defensively."""
    assert infer_surface_for_preview(renderable=None, canonical_operation="unknown") == "syntax_preview"


# ---------------------------------------------------------------------------
# Capability stance contract: SUPPORTED capability without render is a break
# ---------------------------------------------------------------------------


def test_supported_capability_without_render_breaks_silently_in_old_observation() -> None:
    """The recorder's observed_capabilities is the smoking gun for an unimplemented stance.

    A SUPPORTED declaration paired with an empty observed set is the
    exact failure mode the S-5 contract exists to detect. The recorder
    itself does not raise; the smoke-grading layer is responsible
    for comparing the declaration against the recorder and emitting
    a break. This test pins the recorder-only contract: the
    observed set is empty when no preview was rendered, regardless
    of the agent's declaration.
    """
    recorder = CapabilityObservationRecorder()
    supported = DisplayCapabilityStance.supported(
        DisplayCapability.SYNTAX_HIGHLIGHTING, detail="fixture:agy_wire.jsonl"
    )
    assert supported.is_supported
    assert DisplayCapability.SYNTAX_HIGHLIGHTING not in recorder.observed_capabilities()


def test_recorder_observation_carries_unit_id_for_smoke_grading() -> None:
    """The smoke harness keys observations by ``unit_id`` so per-unit breaks can be reported."""
    recorder = CapabilityObservationRecorder()
    recorder.record(
        CapabilityObservation(
            capability=DisplayCapability.SYNTAX_HIGHLIGHTING,
            tool_name="write",
            unit_id="smoke-unit-1",
        )
    )
    obs = recorder.observations_for_capability(DisplayCapability.SYNTAX_HIGHLIGHTING)
    assert obs[0].unit_id == "smoke-unit-1"


# ---------------------------------------------------------------------------
# Integration with PreviewPayload: full route
# ---------------------------------------------------------------------------


def test_full_route_write_operation_records_syntax_capability() -> None:
    """The complete path: PreviewPayload.operation -> surface -> capability."""
    payload = PreviewPayload(
        path="x.py",
        language_hint="python",
        operation="write",
        content="value = 1\n",
    )
    surface = infer_surface_for_preview(renderable=None, canonical_operation=payload.operation)
    capability = capability_for_render(surface_name=surface, tool_name="write")
    assert capability is DisplayCapability.SYNTAX_HIGHLIGHTING

    recorder = CapabilityObservationRecorder()
    recorder.record(CapabilityObservation(capability=capability, tool_name="write", unit_id="u"))
    assert capability in recorder.observed_capabilities()


def test_full_route_read_operation_records_file_capability() -> None:
    payload = PreviewPayload(
        path="x.py",
        language_hint="python",
        operation="read",
        content="value = 1\n",
    )
    surface = infer_surface_for_preview(renderable=None, canonical_operation=payload.operation)
    capability = capability_for_render(surface_name=surface, tool_name="read")
    assert capability is DisplayCapability.FILE_PREVIEW


def test_full_route_replace_operation_records_diff_capability() -> None:
    payload = PreviewPayload(
        path="x.py",
        language_hint="python",
        operation="replace",
        hunks=(),  # Empty hunks here; only the surface inference is under test
    )
    surface = infer_surface_for_preview(renderable=None, canonical_operation=payload.operation)
    capability = capability_for_render(surface_name=surface, tool_name="edit")
    assert capability is DisplayCapability.EDIT_DIFF
