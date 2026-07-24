"""Round-trip the markdown plan edit flow named by the shipped skill."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from pydantic import TypeAdapter

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.coordination import CoordinationSessionLike, ToolContent, WorkspaceLike
from ralph.mcp.tools.md_artifact import (
    handle_edit_md_plan_step,
    handle_stage_md_artifact,
    handle_verify_md_artifact,
)
from ralph.skills._content import get_skill_content
from tests.mcp.test_md_plan_spec import _plan_document
from tests.test_artifact_format_docs_memory_backend import MemoryBackend
from tests.test_artifact_format_docs_mock_session import planning_session

_JSON_OBJECT = TypeAdapter(dict[str, object])


def _skill_body() -> str:
    return get_skill_content("submit-plan-step-edits")


def _session() -> CoordinationSessionLike:
    return cast("CoordinationSessionLike", planning_session())


class _Workspace:
    def absolute_path(self, path: str) -> str:
        return str(Path("/workspace") / path)


def _workspace() -> WorkspaceLike:
    return cast("WorkspaceLike", _Workspace())


def test_skill_names_the_native_markdown_edit_and_submission_flow() -> None:
    body = _skill_body()

    assert "ralph_edit_md_plan_step" in body
    assert "ralph_finalize_md_artifact" in body
    assert "replacement: |" in body
    assert "### [S-2]" in body
    assert "ralph_submit_plan_section" not in body
    assert "ralph_patch_step" not in body


def test_tool_result_round_trips_through_the_shared_markdown_validator() -> None:
    deps = ArtifactHandlerDeps(backend=MemoryBackend())
    handle_stage_md_artifact(
        _session(),
        _workspace(),
        {"artifact_type": "plan", "content": _plan_document()},
        deps=deps,
    )
    result = handle_edit_md_plan_step(
        _session(),
        _workspace(),
        {
            "action": "replace",
            "step_id": "S-2",
            "replacement": (
                "### [S-2] Verify the edited plan\n"
                "Validate the edited document through the public tool.\n\n"
                "Type: verify\n"
                "Depends on: S-1\n"
                "Verify: pytest tests/test_plan_edit_skill_round_trip.py -q\n"
            ),
        },
        deps=deps,
    )
    block = result.content[0]
    assert isinstance(block, ToolContent)
    payload = _JSON_OBJECT.validate_json(block.text)
    edited = payload["content"]
    assert isinstance(edited, str)

    parsed, diagnostics = parse_and_validate(edited, get_spec("plan"))
    verified = handle_verify_md_artifact(
        _session(),
        _workspace(),
        {"artifact_type": "plan", "content": edited},
    )

    assert parsed["steps"]
    assert diagnostics == []
    assert verified.is_error is False
