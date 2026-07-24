"""Regression: the review handoff issue count reads the canonical Markdown artifact.

The pre-migration renderer parsed ``issues.md`` as a legacy JSON envelope; the
parse always failed silently and the phase-close banner reported ``0 issue(s)``
regardless of content. The count must come from the validated Markdown document.
"""

from __future__ import annotations

import types
from typing import TYPE_CHECKING, cast

from ralph.config.enums import Verbosity
from ralph.phases.required_artifacts import RequiredArtifact
from ralph.pipeline.phase_agent_handler import render_success_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.display.context import DisplayContext
    from ralph.display.parallel_display import ParallelDisplay

_ISSUES_DOC = """---
type: issues
status: issues_found
---

## Summary

- [SUM-1] Two real findings.

## Issues

- [I-1] src/app.py | high | Lock entry leaks on the exception path
- [I-2] tests/test_app.py | low | Assertion depends on wall-clock timing

## What Came Up Short

- [W-1] Exception paths were not exercised.

## How To Fix

- [FIX-1] Wrap the critical section in try/finally and add a failure-path test.
"""


def _required_issues_artifact() -> RequiredArtifact:
    return RequiredArtifact(
        phase="review",
        artifact_type="issues",
        artifact_path=".agent/artifacts/issues.md",
        markdown_path=None,
        normalizer=None,
    )


def _render_and_capture(workspace_root: Path) -> dict[str, str]:
    recorded: dict[str, str] = {}

    def _capture_outcome(outcome: str) -> None:
        recorded["outcome"] = outcome

    display = types.SimpleNamespace(record_artifact_outcome=_capture_outcome)
    render_success_artifact(
        "issues",
        workspace_root,
        cast("DisplayContext", types.SimpleNamespace()),
        cast("ParallelDisplay", display),
        Verbosity.VERBOSE,
        _required_issues_artifact(),
    )
    return recorded


def test_issue_count_comes_from_markdown_artifact(tmp_path: Path) -> None:
    artifact = tmp_path / ".agent" / "artifacts" / "issues.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(_ISSUES_DOC, encoding="utf-8")

    recorded = _render_and_capture(tmp_path)

    assert recorded["outcome"] == "2 issue(s)"


def test_issue_count_is_zero_when_artifact_missing(tmp_path: Path) -> None:
    recorded = _render_and_capture(tmp_path)

    assert recorded["outcome"] == "0 issue(s)"


def test_issue_count_ignores_legacy_json_envelope(tmp_path: Path) -> None:
    """A JSON envelope is not a valid Markdown artifact and contributes no count."""
    artifact = tmp_path / ".agent" / "artifacts" / "issues.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        '{"type":"issues","content":{"issues":[{"path":"a","severity":"high"}]}}',
        encoding="utf-8",
    )

    recorded = _render_and_capture(tmp_path)

    assert recorded["outcome"] == "0 issue(s)"
