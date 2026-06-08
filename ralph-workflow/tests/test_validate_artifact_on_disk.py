"""Tests for the single on-disk artifact-contract check, `validate_artifact_on_disk`.

Both the pipeline phase gates and the commit command call this one function to
decide "is the required artifact present, parseable, and the right shape?", so
the missing / can't-parse / wrong-format detection cannot drift between callers.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.phases.artifacts import validate_artifact_on_disk
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable


def _ra(
    normalizer: Callable[[dict[str, object]], dict[str, object]] | None = None,
) -> RequiredArtifact:
    return RequiredArtifact(
        phase="planning",
        artifact_type="plan",
        json_path=".agent/artifacts/plan.json",
        markdown_path=None,
        normalizer=normalizer,
    )


def test_returns_none_when_present_and_valid() -> None:
    ws = MemoryWorkspace()
    ws.write(".agent/artifacts/plan.json", json.dumps({"type": "plan", "content": {"a": 1}}))
    assert validate_artifact_on_disk(ws, _ra()) is None


def test_detail_when_missing() -> None:
    ws = MemoryWorkspace()
    detail = validate_artifact_on_disk(ws, _ra())
    assert detail is not None
    assert "not found" in detail.lower()


def test_detail_when_unparseable() -> None:
    ws = MemoryWorkspace()
    ws.write(".agent/artifacts/plan.json", "{ not json")
    detail = validate_artifact_on_disk(ws, _ra())
    assert detail is not None
    assert "json" in detail.lower()


def test_detail_when_wrong_type() -> None:
    ws = MemoryWorkspace()
    ws.write(".agent/artifacts/plan.json", json.dumps({"type": "issues", "content": {"a": 1}}))
    detail = validate_artifact_on_disk(ws, _ra())
    assert detail is not None
    assert "type" in detail.lower()


def test_detail_when_normalizer_rejects() -> None:
    ws = MemoryWorkspace()
    ws.write(".agent/artifacts/plan.json", json.dumps({"type": "plan", "content": {"a": 1}}))

    def _bad(_content: dict[str, object]) -> dict[str, object]:
        raise ValueError("bad schema: missing field")

    detail = validate_artifact_on_disk(ws, _ra(normalizer=_bad))
    assert detail is not None
    assert "bad schema" in detail
