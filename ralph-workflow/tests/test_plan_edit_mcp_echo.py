"""Wire-result coverage for markdown plan-step edits."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
from pydantic import TypeAdapter

from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.coordination import (
    CoordinationSessionLike,
    InvalidParamsError,
    ToolContent,
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


def _staged_deps() -> ArtifactHandlerDeps:
    deps = ArtifactHandlerDeps(backend=MemoryBackend())
    handle_stage_md_artifact(
        _session(),
        _workspace(),
        {"artifact_type": "plan", "content": _plan_document()},
        deps=deps,
    )
    return deps


def test_edit_tool_returns_the_updated_persisted_draft() -> None:
    result = handle_edit_md_plan_step(
        _session(),
        _workspace(),
        {
            "action": "replace",
            "step_id": "S-2",
            "replacement": (
                "### [S-2] Run focused tests\n"
                "Run the focused migration tests.\n\n"
                "Type: verify\n"
                "Depends on: S-1\n"
                "Verify: pytest tests/test_plan_artifact*.py -q\n"
            ),
        },
        deps=_staged_deps(),
    )

    block = result.content[0]
    assert isinstance(block, ToolContent)
    payload = _JSON_OBJECT.validate_json(block.text)
    edited = payload["content"]
    assert isinstance(edited, str)
    assert payload["exists"] is True
    assert "### [S-2] Run focused tests" in edited
    assert "Depends on: S-1" in edited


def test_edit_tool_rejects_a_full_document_passed_as_content() -> None:
    with pytest.raises(InvalidParamsError, match="not an accepted argument"):
        handle_edit_md_plan_step(
            _session(),
            _workspace(),
            {
                "content": _plan_document(),
                "action": "replace",
                "step_id": "S-2",
                "replacement": "### [S-2] Anything\nBody.\n\nType: action\n",
            },
            deps=_staged_deps(),
        )


@pytest.mark.parametrize(
    ("params", "message"),
    [
        ({"step_id": "S-1"}, "action and step_id"),
        (
            {
                "action": "replace",
                "step_id": "S-1",
                "replacement": {"title": "JSON is retired"},
            },
            "replacement must be a markdown step block",
        ),
        (
            {
                "action": "move",
                "step_id": "S-1",
                "index": "1",
            },
            "index must be an integer",
        ),
    ],
)
def test_edit_tool_rejects_non_markdown_wire_shapes(
    params: dict[str, object], message: str
) -> None:
    with pytest.raises(InvalidParamsError, match=message):
        handle_edit_md_plan_step(
            _session(),
            _workspace(),
            params,
            deps=_staged_deps(),
        )
