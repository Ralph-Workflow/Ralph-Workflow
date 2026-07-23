"""Markdown draft handler behavior."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.tools.md_artifact import (
    handle_discard_md_draft,
    handle_get_md_draft,
    handle_stage_md_artifact,
)
from tests.test_artifact_format_docs_mock_session import planning_session
from tests.test_artifact_format_docs_mock_workspace import MockWorkspace

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination import ToolResult
    from ralph.mcp.tools.tool_content import ToolContent

_PARTIAL = "---\ntype: product_spec\n---\n## Title\n- [T1] Markdown artifacts\n"


def _payload(result: ToolResult) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(cast("ToolContent", result.content[0]).text))


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
