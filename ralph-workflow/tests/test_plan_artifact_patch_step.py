"""Stable-ID plan-step edit coverage for the markdown tool."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter

from ralph.mcp.artifacts.markdown import MarkdownArtifactError, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    ToolContent,
    ToolResult,
    WorkspaceLike,
)
from ralph.mcp.tools.md_artifact import handle_edit_md_plan_step, handle_stage_md_artifact
from tests.mcp.test_md_plan_spec import _plan_document
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_session import planning_session

_JSON_OBJECT = TypeAdapter(dict[str, object])


class _Workspace:
    def absolute_path(self, path: str) -> str:
        return str(Path("/workspace") / path)


def _session() -> CoordinationSessionLike:
    return cast("CoordinationSessionLike", planning_session())


def _workspace() -> WorkspaceLike:
    return cast("WorkspaceLike", _Workspace())


def _staged_deps(document: str) -> ArtifactHandlerDeps:
    deps = ArtifactHandlerDeps(backend=MemoryBackend())
    handle_stage_md_artifact(
        _session(),
        _workspace(),
        {"artifact_type": "plan", "content": document},
        deps=deps,
    )
    return deps


def _edited_content(result: ToolResult) -> str:
    block = result.content[0]
    assert isinstance(block, ToolContent)
    payload = _JSON_OBJECT.validate_json(block.text)
    edited = payload["content"]
    assert isinstance(edited, str)
    return edited


def test_replace_step_uses_a_markdown_block_and_preserves_stable_references() -> None:
    replacement = """### [S-2] Verify the complete focused suite
Run every plan markdown migration test.

Type: verify
Depends on: S-1
Verify: pytest tests/test_plan_artifact*.py -q
"""

    result = handle_edit_md_plan_step(
        _session(),
        _workspace(),
        {
            "action": "replace",
            "step_id": "S-2",
            "replacement": replacement,
        },
        deps=_staged_deps(_plan_document()),
    )

    edited = _edited_content(result)
    content, diagnostics = parse_and_validate(edited, get_spec("plan"))
    steps = cast("list[dict[str, object]]", content["steps"])
    assert diagnostics == []
    assert steps[1]["number"] == 2
    assert steps[1]["depends_on"] == [1]
    assert steps[1]["title"] == "Verify the complete focused suite"


def test_insert_and_move_keep_ids_instead_of_renumbering() -> None:
    deps = _staged_deps(_plan_document())
    handle_edit_md_plan_step(
        _session(),
        _workspace(),
        {
            "action": "insert",
            "step_id": "S-3",
            "index": 2,
            "replacement": (
                "### [S-3] Review the grammar\n"
                "Review the native markdown fields.\n\n"
                "Type: action\n"
                "Depends on: S-1\n"
            ),
        },
        deps=deps,
    )
    moved = handle_edit_md_plan_step(
        _session(),
        _workspace(),
        {
            "action": "move",
            "step_id": "S-3",
            "index": 1,
        },
        deps=deps,
    )

    content, diagnostics = parse_and_validate(_edited_content(moved), get_spec("plan"))
    steps = cast("list[dict[str, object]]", content["steps"])
    assert diagnostics == []
    assert [step["number"] for step in steps] == [3, 1, 2]
    assert steps[0]["depends_on"] == [1]
    assert steps[2]["depends_on"] == [1]


def test_remove_referenced_step_is_rejected() -> None:
    with pytest.raises(MarkdownArtifactError) as excinfo:
        handle_edit_md_plan_step(
            _session(),
            _workspace(),
            {"action": "remove", "step_id": "S-1"},
            deps=_staged_deps(_plan_document()),
        )

    assert any(item.rule_id == "PLAN021" for item in excinfo.value.diagnostics)


def test_replacement_block_id_must_match_addressed_step() -> None:
    with pytest.raises(ValueError, match="must match step_id"):
        handle_edit_md_plan_step(
            _session(),
            _workspace(),
            {
                "action": "replace",
                "step_id": "S-2",
                "replacement": "### [S-9] Wrong step\nBody.\n\nType: action\n",
            },
            deps=_staged_deps(_plan_document()),
        )
