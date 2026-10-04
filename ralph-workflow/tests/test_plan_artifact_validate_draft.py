from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.md_artifact import (
    handle_get_md_draft,
    handle_stage_md_artifact,
    handle_submit_md_artifact,
    handle_verify_md_artifact,
)
from ralph.workspace.fs import FsWorkspace
from tests._artifact_format_docs_mock_session import planning_session
from tests.mcp.test_md_plan_spec import _plan_document

if TYPE_CHECKING:
    from pathlib import Path

from pydantic import TypeAdapter

_JSON_OBJECT = TypeAdapter(dict[str, object])


def test_get_plan_draft_preserves_dangling_references_without_diagnostics(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    session = planning_session()
    document = _plan_document().replace("Depends on: S-1", "Depends on: S-99")
    handle_stage_md_artifact(session, workspace, {"artifact_type": "plan", "content": document})

    result = handle_get_md_draft(session, workspace, {"artifact_type": "plan"})
    payload = _JSON_OBJECT.validate_json(result.content[0].text)

    assert payload["valid"] is True
    assert payload["content"] == document
    assert payload["diagnostics"] == []


@pytest.mark.parametrize("section", ["Work Units", "Parallel Plan"])
def test_plan_submission_does_not_load_execution_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    section: str,
) -> None:
    from ralph.policy import loader

    def unavailable_policy(_config_dir: Path) -> object:
        raise OSError("policy is unreadable")

    monkeypatch.setattr(loader, "load_policy", unavailable_policy)
    document = f"## {section}\n- [U-1] Work\n  Directories: .agent\n  Depends on: missing\n"
    workspace = FsWorkspace(tmp_path)
    session = planning_session()
    params = {"artifact_type": "plan", "content": document}

    for handler in (handle_verify_md_artifact, handle_submit_md_artifact):
        result = handler(session, workspace, params)
        payload = _JSON_OBJECT.validate_json(result.content[0].text)
        assert result.is_error is False
        assert payload["valid"] is True
        assert payload["diagnostics"] == []

    assert workspace.read(".agent/artifacts/plan.md") == document
