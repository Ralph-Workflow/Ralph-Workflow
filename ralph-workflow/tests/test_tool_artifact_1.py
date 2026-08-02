"""Canonical markdown handoff and draft handler behavior.

Consolidated from ``test_tool_artifact_1.py`` and ``test_tool_artifact_2.py``.
The two files both exercise ``ralph.mcp.tools.md_artifact`` against the
in-memory ``MockWorkspace`` and ``planning_session`` helpers; merging them
keeps the shared imports paid once per shard and preserves a single
black-box contract for the markdown artifact tool surface.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.handoffs import HANDOFF_PATHS
from ralph.mcp.tools.md_artifact import (
    handle_discard_md_draft,
    handle_get_md_draft,
    handle_stage_md_artifact,
    handle_submit_md_artifact,
)
from tests._support.typed_accessors import must_mapping, must_text
from tests._artifact_format_docs_mock_session import planning_session
from tests._artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination import ToolResult

_DOCUMENT = """---
type: development_result
status: completed
---
## Summary
- [S1] Markdown migration completed.
## Files Changed
- [F1] tests/test_tool_artifact_1.py
## Plan Items Proven
- [S-1] Focused tests pass.
"""

_PARTIAL = "---\ntype: product_spec\n---\n## Title\n- [T1] Markdown artifacts\n"


def _payload(result: ToolResult) -> dict[str, object]:
    return must_mapping(json.loads(must_text(result.content[0])))


def test_submission_writes_byte_identical_artifact_and_handoff(tmp_path: Path) -> None:
    result = handle_submit_md_artifact(
        planning_session(drain="development"),
        MockWorkspace(tmp_path),
        {"artifact_type": "development_result", "content": _DOCUMENT},
    )

    artifact = tmp_path / ".agent" / "artifacts" / "development_result.md"
    handoff = tmp_path / HANDOFF_PATHS["development_result"]
    assert result.is_error is False
    assert artifact.read_text(encoding="utf-8") == _DOCUMENT
    assert handoff.read_text(encoding="utf-8") == _DOCUMENT


def test_partial_draft_can_be_resumed_then_discarded(tmp_path: Path) -> None:
    session = planning_session()
    workspace = MockWorkspace(tmp_path)

    staged = handle_stage_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": _PARTIAL},
    )
    resumed = handle_get_md_draft(
        session,
        workspace,
        {"artifact_type": "product_spec"},
    )
    discarded = handle_discard_md_draft(
        session,
        workspace,
        {"artifact_type": "product_spec"},
    )

    assert staged.is_error is False
    assert _payload(resumed)["content"] == _PARTIAL
    assert _payload(discarded)["discarded"] is True
