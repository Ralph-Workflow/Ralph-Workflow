from __future__ import annotations

import concurrent.futures
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.canonical_submit import (
    _artifact_dir,
    history_dir_for_artifact,
    submit_artifact_canonical,
)
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown import specs as _markdown_specs
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.md_draft_io import load_md_draft
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.md_artifact import handle_submit_md_artifact
from tests._tool_artifact_2_helper_mocksession import MockSession
from tests._tool_artifact_2_helper_mockworkspace import MockWorkspace

if TYPE_CHECKING:
    from ralph.mcp.tools.coordination import ToolResult

assert _markdown_specs is not None


VALID_DEVELOPMENT_RESULT = """\
---
type: development_result
status: completed
---

## Summary

- [SUM-1] Implementation completed successfully.

## Files Changed

- [F-1] ralph/example.py

## Plan Items Proven

- [S-1] All verification criteria met.
  Disposition: completed

## Analysis Items Addressed

- [FIX-1] Idempotent submission verified.
"""


def test_duplicate_canonical_submission_runs_exactly_once(tmp_path: Path) -> None:
    spec = get_spec("development_result")
    parsed, diagnostics = parse_and_validate(VALID_DEVELOPMENT_RESULT, spec)
    assert not any(d.severity == "error" for d in diagnostics)

    run_id = "run-e14-dedupe"
    deps = ArtifactHandlerDeps(history_enabled=True)

    res1 = submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content=parsed,
        markdown=VALID_DEVELOPMENT_RESULT,
        run_id=run_id,
        deps=deps,
    )
    assert res1.artifact_path is not None
    assert res1.artifact_path.exists()
    assert res1.receipt_path is not None
    assert res1.receipt_path.exists()

    canonical_file = _artifact_dir(tmp_path) / "development_result.md"
    assert canonical_file.exists()
    first_content = canonical_file.read_text(encoding="utf-8")
    assert first_content == VALID_DEVELOPMENT_RESULT

    res2 = submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content=parsed,
        markdown=VALID_DEVELOPMENT_RESULT,
        run_id=run_id,
        deps=deps,
    )
    assert res2.artifact_path is not None
    assert res2.artifact_path.exists()
    assert res2.receipt_path is not None
    assert res2.receipt_path.exists()

    second_content = canonical_file.read_text(encoding="utf-8")
    assert second_content == first_content

    history_dir = history_dir_for_artifact(_artifact_dir(tmp_path), "development_result")
    assert not history_dir.exists(), "Duplicate submission created spurious history directory!"

    assert artifact_receipt_present(tmp_path, run_id, "development_result") is True


def test_duplicate_submission_via_handle_submit_md_artifact(tmp_path: Path) -> None:
    session = MockSession(drain="development")
    workspace = MockWorkspace(tmp_path)
    params = {
        "artifact_type": "development_result",
        "content": VALID_DEVELOPMENT_RESULT,
    }

    result1: ToolResult = handle_submit_md_artifact(session, workspace, params)
    assert result1.is_error is False

    result2: ToolResult = handle_submit_md_artifact(session, workspace, params)
    assert result2.is_error is False

    draft = load_md_draft(_artifact_dir(tmp_path), "development_result")
    assert draft == VALID_DEVELOPMENT_RESULT

    canonical_file = _artifact_dir(tmp_path) / "development_result.md"
    assert canonical_file.exists()
    assert canonical_file.read_text(encoding="utf-8") == VALID_DEVELOPMENT_RESULT


def test_concurrent_duplicate_submissions_run_safely(tmp_path: Path) -> None:
    spec = get_spec("development_result")
    parsed, diagnostics = parse_and_validate(VALID_DEVELOPMENT_RESULT, spec)
    assert not any(d.severity == "error" for d in diagnostics)

    run_id = "run-e14-concurrent"
    deps = ArtifactHandlerDeps(history_enabled=True)

    submit_artifact_canonical(
        workspace_root=tmp_path,
        artifact_type="development_result",
        parsed_content=parsed,
        markdown=VALID_DEVELOPMENT_RESULT,
        run_id=run_id,
        deps=deps,
    )

    def _worker() -> None:
        submit_artifact_canonical(
            workspace_root=tmp_path,
            artifact_type="development_result",
            parsed_content=parsed,
            markdown=VALID_DEVELOPMENT_RESULT,
            run_id=run_id,
            deps=deps,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_worker) for _ in range(5)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    canonical_file = _artifact_dir(tmp_path) / "development_result.md"
    assert canonical_file.exists()
    assert canonical_file.read_text(encoding="utf-8") == VALID_DEVELOPMENT_RESULT

    history_dir = history_dir_for_artifact(_artifact_dir(tmp_path), "development_result")
    assert not history_dir.exists()
