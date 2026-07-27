"""Production-path regression replay for the repaired Pi record corpus."""

from __future__ import annotations

from pathlib import Path

from tests.display.test_raw_record_regression import _drive_fixture_through_production


def test_real_corpus_regression_replays_repaired_shape(tmp_path: Path) -> None:
    """S-2: a defect-shaped Pi slice stays repaired on both surfaces."""
    rendered, live = _drive_fixture_through_production(
        "pi_real_corpus_defects.jsonl", tmp_path, unit_id="pi"
    )
    duplicate = "Command: cd /Volumes/Crucial\\ X9/ext-Projects/Ralph-Workflow/wt-028-display"
    assert rendered.count(duplicate) == live.count(duplicate) == 1
    for surface in (rendered, live):
        assert "[??:??:??]" not in surface
        assert "role=progress" not in surface
        assert "thinking:" not in surface
        assert "⚠ WARN" not in surface
    assert "truncated, 1 line" in rendered
    assert " B, see .agent/raw/pi.log" in rendered
