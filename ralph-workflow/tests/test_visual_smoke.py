from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from ralph.visual.design_verdict import validate_deterministic_capture_evidence

MEDIA_URI_RE = re.compile(r"^ralph://media/[^/\s]+$")


def _validate_visual_evidence(verdict: dict[str, object]) -> list[str]:
    """Lightweight validator for visual-evidence dicts in the smoke test."""
    errors: list[str] = []
    for field_name in ("design_verdict_id", "before_set_id", "after_set_id"):
        if not verdict.get(field_name):
            errors.append(f"{field_name} is required")
    handles = verdict.get("cell_handles", ())
    if isinstance(handles, str):
        handles = (handles,)
    if not handles:
        errors.append("cell_handles is required")
    elif any(not MEDIA_URI_RE.fullmatch(str(h)) for h in handles):
        errors.append("cell_handles must contain only ralph://media/... URIs")
    return errors


FIXTURE_DIR = Path(__file__).parent / "_fixtures" / "visual"


class StubJudge:
    def judge(self, before: dict, after: dict) -> dict:
        if before == after:
            return {
                "visual_verdict": "improved",
                "design_verdict_id": "unchanged",
                "run_id": "run-13",
                "before_set_id": before["capture_set_id"],
                "after_set_id": after["capture_set_id"],
                "cell_handles": [cell["path"] for cell in before["cells"]],
            }
        return {
            "visual_verdict": "improved",
            "design_verdict_id": "verdict-13",
            "run_id": "run-13",
            "before_set_id": before["capture_set_id"],
            "after_set_id": after["capture_set_id"],
            "cell_handles": [cell["path"] for cell in after["cells"]],
        }


def _captures() -> tuple[dict, dict]:
    return (
        json.loads((FIXTURE_DIR / "before.json").read_text()),
        json.loads((FIXTURE_DIR / "after.json").read_text()),
    )


def _validate_fixture_verdict(
    before: dict[str, object], after: dict[str, object], verdict: dict[str, object]
) -> list[str]:
    try:
        validate_deterministic_capture_evidence(before, after, verdict)
    except ValueError as exc:
        return [str(exc)]
    return []


@pytest.mark.smoke
@pytest.mark.subprocess_e2e
def test_stub_judge_submits_visual_evidence_with_capture_handles() -> None:
    before, after = _captures()
    verdict = StubJudge().judge(before, after)
    assert verdict["cell_handles"]
    assert all(handle.startswith("ralph://media/") for handle in verdict["cell_handles"])
    assert _validate_visual_evidence(verdict) == []
    assert _validate_fixture_verdict(before, after, verdict) == []


@pytest.mark.smoke
@pytest.mark.subprocess_e2e
def test_verdict_without_capture_fails() -> None:
    before, after = _captures()
    verdict = StubJudge().judge(before, after)
    verdict.pop("cell_handles")
    assert "cell_handles is required" in _validate_visual_evidence(verdict)
    assert "capture handles are required" in _validate_fixture_verdict(before, after, verdict)


@pytest.mark.smoke
@pytest.mark.subprocess_e2e
def test_improved_verdict_on_unchanged_captures_fails() -> None:
    before, _ = _captures()
    verdict = StubJudge().judge(before, before)
    assert verdict["visual_verdict"] == "improved"
    assert "design_verdict_id" not in verdict or verdict["design_verdict_id"] == "unchanged"
    assert any(
        "byte-identical" in error
        for error in _validate_fixture_verdict(before, before, verdict)
    )
