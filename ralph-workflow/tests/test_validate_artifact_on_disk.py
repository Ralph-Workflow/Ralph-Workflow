"""Tests for the single on-disk artifact-contract check, `validate_artifact_on_disk`.

Both the pipeline phase gates and the commit command call this one function to
decide "is the required artifact present, parseable, and the right shape?", so
the missing / can't-parse / wrong-format detection cannot drift between callers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.phases.artifacts import validate_artifact_on_disk
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable


VALID_FIX_RESULT = """\
---
type: fix_result
---
## Summary
- [S1] Fixed validation
## Files Changed
- [F1] ralph/phases/artifacts.py
"""


def _ra(
    normalizer: Callable[[dict[str, object]], dict[str, object]] | None = None,
) -> RequiredArtifact:
    return RequiredArtifact(
        phase="fix",
        artifact_type="fix_result",
        artifact_path=".agent/artifacts/fix_result.md",
        markdown_path=None,
        normalizer=normalizer,
    )


def test_returns_none_when_present_and_valid() -> None:
    ws = MemoryWorkspace()
    ws.write(".agent/artifacts/fix_result.md", VALID_FIX_RESULT)
    assert validate_artifact_on_disk(ws, _ra()) is None


def test_detail_when_missing() -> None:
    ws = MemoryWorkspace()
    detail = validate_artifact_on_disk(ws, _ra())
    assert detail is not None
    assert "not found" in detail.lower()


def test_detail_when_unparseable() -> None:
    ws = MemoryWorkspace()
    ws.write(".agent/artifacts/fix_result.md", "---\ntype: [\n---\n")
    detail = validate_artifact_on_disk(ws, _ra())
    assert detail is not None
    assert "invalid" in detail.lower()


def test_detail_when_wrong_type() -> None:
    ws = MemoryWorkspace()
    ws.write(
        ".agent/artifacts/fix_result.md",
        """\
---
type: issues
---
## Summary
Review found issues.
## Issues
- [ISS-1] Fix artifact loading
  Severity: high
  Location: ralph/phases/artifacts.py
  Evidence: JSON was accepted
  How to fix: reject legacy JSON
""",
    )
    detail = validate_artifact_on_disk(ws, _ra())
    assert detail is not None
    assert "type" in detail.lower()


def test_detail_when_normalizer_rejects() -> None:
    ws = MemoryWorkspace()
    ws.write(".agent/artifacts/fix_result.md", VALID_FIX_RESULT)

    def _bad(_content: dict[str, object]) -> dict[str, object]:
        raise ValueError("bad schema: missing field")

    detail = validate_artifact_on_disk(ws, _ra(normalizer=_bad))
    assert detail is not None
    assert "bad schema" in detail
