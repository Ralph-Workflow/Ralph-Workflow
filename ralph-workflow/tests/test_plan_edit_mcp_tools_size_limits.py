"""Size-boundary coverage for markdown plan step edits and draft staging."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.mcp.tools.invalid_params_error import InvalidParamsError
from ralph.mcp.tools.md_artifact import handle_edit_md_plan_step, handle_stage_md_artifact

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.tool_content import ToolContent

from tests.test_plan_edit_mcp_tools import _PLAN


class _Session:
    session_id = "test"
    run_id = ""
    explore_index: object | None = None
    broker_secret: str | None = None

    def check_capability(self, capability: str) -> object:
        return capability in {"artifact.submit", "artifact.plan_read"}


class _Workspace:
    def __init__(self, root: Path) -> None:
        self.root = root

    def absolute_path(self, path: str) -> str:
        return str(self.root / path)


def _edit(replacement: str) -> str:
    result = handle_edit_md_plan_step(
        _Session(),
        cast("object", None),
        {
            "content": _PLAN,
            "action": "insert",
            "step_id": "S-9",
            "replacement": replacement,
        },
    )
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    return cast("str", payload["content"])


def test_step_with_near_limit_prose_round_trips() -> None:
    prose = "x" * 19_999
    replacement = f"""### [S-9] Long content
{prose}

Type: action"""

    edited = _edit(replacement)

    assert prose in edited
    assert "### [S-9] Long content" in edited


def test_step_with_500_evidence_entries_round_trips() -> None:
    evidence = "\n".join(f"- file: src/file_{index}.py" for index in range(500))
    replacement = f"""### [S-9] Evidence-heavy step
Record bounded evidence.

Type: file_change
Files:
- modify src/main.py
Evidence:
{evidence}"""

    edited = _edit(replacement)

    assert edited.count("- file: src/file_") == 500


def test_staged_plan_over_four_megabytes_is_rejected_without_overwriting_draft(
    tmp_path: Path,
) -> None:
    workspace = _Workspace(tmp_path)
    handle_stage_md_artifact(
        _Session(),
        workspace,
        {"artifact_type": "plan", "content": _PLAN, "mode": "replace_all"},
    )

    with pytest.raises(InvalidParamsError, match="character cap"):
        handle_stage_md_artifact(
            _Session(),
            workspace,
            {"artifact_type": "plan", "content": "x" * 4_000_001},
        )

    draft = tmp_path / ".agent" / "artifacts" / ".plan.draft.md"
    assert draft.read_text(encoding="utf-8") == _PLAN
