"""Black-box test: recovery fields round-trip through checkpoint save/load."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.pipeline.checkpoint import load, save
from ralph.pipeline.state import AgentChainState, FalloverRecord, PipelineState

if TYPE_CHECKING:
    from pathlib import Path


def _make_state_with_recovery_fields() -> PipelineState:
    return PipelineState(
        phase="development",
        phase_chains={
            "development": AgentChainState(
                agents=["claude", "opencode"], current_index=1, retries=2
            )
        },
        recovery_cycle_count=3,
        fallover_history=(
            FalloverRecord(
                phase="development",
                from_agent="claude",
                to_agent="opencode",
                timestamp_iso="2026-04-23T00:00:00+00:00",
            ),
        ),
        last_failure_category="agent",
        last_connectivity_state="online",
        recovery_cycle_cap=10,
        last_retry_delay_ms=500,
    )


def test_recovery_fields_round_trip(tmp_path: Path) -> None:
    """All recovery fields survive a save/load cycle with exact equality."""
    ckpt_path = tmp_path / "checkpoint.json"
    original = _make_state_with_recovery_fields()

    save(original, ckpt_path)
    loaded = load(ckpt_path)

    assert loaded is not None
    assert loaded.recovery_cycle_count == 3  # noqa: PLR2004
    assert loaded.last_failure_category == "agent"
    assert loaded.last_connectivity_state == "online"
    assert loaded.recovery_cycle_cap == 10  # noqa: PLR2004
    assert loaded.last_retry_delay_ms == 500  # noqa: PLR2004
    assert len(loaded.fallover_history) == 1
    record = loaded.fallover_history[0]
    assert record.phase == "development"
    assert record.from_agent == "claude"
    assert record.to_agent == "opencode"


def test_recovery_fields_full_equality(tmp_path: Path) -> None:
    """Saved and loaded recovery fields are exactly equal."""
    ckpt_path = tmp_path / "checkpoint.json"
    original = _make_state_with_recovery_fields()

    save(original, ckpt_path)
    loaded = load(ckpt_path)

    assert loaded is not None
    assert loaded.recovery_cycle_count == original.recovery_cycle_count
    assert loaded.last_failure_category == original.last_failure_category
    assert loaded.last_connectivity_state == original.last_connectivity_state
    assert loaded.recovery_cycle_cap == original.recovery_cycle_cap
    assert loaded.last_retry_delay_ms == original.last_retry_delay_ms
    assert loaded.fallover_history == original.fallover_history


def test_older_checkpoint_missing_recovery_fields_loads_with_defaults(tmp_path: Path) -> None:
    """A checkpoint JSON without recovery fields loads cleanly using defaults."""
    ckpt_path = tmp_path / "checkpoint.json"
    # Write a minimal checkpoint JSON without recovery fields (simulating an older checkpoint)
    minimal_json = json.dumps(
        {
            "phase": "development",
            "previous_phase": None,
            "iteration": 1,
            "total_iterations": 3,
            "reviewer_pass": 0,
            "total_reviewer_passes": 1,
            "last_error": None,
        }
    )
    ckpt_path.write_text(minimal_json, encoding="utf-8")

    loaded = load(ckpt_path)

    assert loaded is not None
    assert loaded.phase == "development"
    assert loaded.recovery_cycle_count == 0
    assert loaded.last_failure_category is None
    assert loaded.last_connectivity_state == "unknown"
    assert loaded.recovery_cycle_cap >= 1
    assert loaded.last_retry_delay_ms == 0
    assert loaded.fallover_history == ()


def test_multiple_fallover_records_preserved(tmp_path: Path) -> None:
    """Multiple fallover history entries are all preserved through save/load."""
    ckpt_path = tmp_path / "checkpoint.json"
    state = PipelineState(
        phase="fix",
        fallover_history=(
            FalloverRecord(
                phase="development",
                from_agent="claude",
                to_agent="opencode",
                timestamp_iso="2026-04-23T00:00:00+00:00",
            ),
            FalloverRecord(
                phase="fix",
                from_agent="opencode",
                to_agent="claude",
                timestamp_iso="2026-04-23T00:01:00+00:00",
            ),
        ),
        recovery_cycle_count=2,
    )

    save(state, ckpt_path)
    loaded = load(ckpt_path)

    assert loaded is not None
    assert len(loaded.fallover_history) == 2  # noqa: PLR2004
    assert loaded.fallover_history[0].from_agent == "claude"
    assert loaded.fallover_history[1].from_agent == "opencode"
    assert loaded.recovery_cycle_count == 2  # noqa: PLR2004


def test_zero_recovery_fields_round_trip(tmp_path: Path) -> None:
    """Default (zero/empty) recovery fields survive save/load unchanged."""
    ckpt_path = tmp_path / "checkpoint.json"
    state = PipelineState(phase="planning")

    save(state, ckpt_path)
    loaded = load(ckpt_path)

    assert loaded is not None
    assert loaded.recovery_cycle_count == 0
    assert loaded.last_failure_category is None
    assert loaded.last_connectivity_state == "unknown"
    assert loaded.recovery_cycle_cap == 200  # noqa: PLR2004
    assert loaded.last_retry_delay_ms == 0
    assert loaded.fallover_history == ()


def test_over_cap_recovery_history_loads_with_newest_records_only(tmp_path: Path) -> None:
    """An over-cap checkpoint payload loads back with exactly the newest retained records."""
    ckpt_path = tmp_path / "checkpoint.json"
    payload = json.dumps(
        {
            "phase": "development",
            "recovery_cycle_cap": 2,
            "fallover_history": [
                {
                    "phase": "development",
                    "from_agent": "a1",
                    "to_agent": "b1",
                    "timestamp_iso": "2026-04-21T00:00:01Z",
                },
                {
                    "phase": "development",
                    "from_agent": "a2",
                    "to_agent": "b2",
                    "timestamp_iso": "2026-04-21T00:00:02Z",
                },
                {
                    "phase": "development",
                    "from_agent": "a3",
                    "to_agent": "b3",
                    "timestamp_iso": "2026-04-21T00:00:03Z",
                },
            ],
        }
    )
    ckpt_path.write_text(payload, encoding="utf-8")

    loaded = load(ckpt_path)

    assert loaded is not None
    assert loaded.recovery_cycle_cap == 2  # noqa: PLR2004
    assert [record.from_agent for record in loaded.fallover_history] == ["a2", "a3"]
