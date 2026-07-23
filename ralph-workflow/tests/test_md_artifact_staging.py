"""Incremental staging endpoints for markdown artifact drafts."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

import pytest

from ralph.config.mcp_models import McpConfig
from ralph.mcp.tools._side_effects import REGISTRY
from ralph.mcp.tools.bridge import tool_specs
from ralph.mcp.tools.invalid_params_error import InvalidParamsError
from ralph.mcp.tools.md_artifact import (
    handle_discard_md_draft,
    handle_finalize_md_artifact,
    handle_get_md_draft,
    handle_stage_md_artifact,
    handle_verify_md_artifact,
)
from ralph.mcp.tools.names import (
    DISCARD_MD_DRAFT_TOOL,
    FINALIZE_MD_ARTIFACT_TOOL,
    GET_MD_DRAFT_TOOL,
    STAGE_MD_ARTIFACT_TOOL,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination import ToolResult
    from ralph.mcp.tools.tool_content import ToolContent


class MockSession:
    """Typed stand-in satisfying CoordinationSessionLike for handler tests."""

    session_id: str = "test-session"
    #: Empty run id keeps canonical submission off the receipt/sentinel path.
    run_id: str = ""
    explore_index: object | None = None

    @property
    def broker_secret(self) -> str | None:
        return None

    def check_capability(self, capability: str) -> object:
        return capability in {"artifact.submit", "artifact.plan_read"}


class MockWorkspace:
    """Typed stand-in satisfying WorkspaceLike rooted at a tmp_path."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def absolute_path(self, path: str) -> str:
        return str(self.root / path)

_HEAD = """---
type: product_spec
---
## Title
- [T1] Markdown artifacts
## Scope
- [S1] Move artifacts to markdown
"""

_TAIL = """## Goals
- [G1] Reduce authoring friction
## Users
- [U1] Agents
## Success Criteria
- [C1] Markdown validates
"""


def _payload(result: ToolResult) -> dict[str, object]:
    content = cast("ToolContent", result.content[0])
    return cast("dict[str, object]", json.loads(content.text))


def _diagnostic_rules(payload: dict[str, object]) -> set[object]:
    diagnostics = cast("list[dict[str, object]]", payload["diagnostics"])
    return {diagnostic["rule_id"] for diagnostic in diagnostics}


def test_stage_appends_and_reports_non_gating_diagnostics(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)

    staged = handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _HEAD}
    )

    assert staged.is_error is False
    payload = _payload(staged)
    assert payload["artifact_type"] == "product_spec"
    assert payload["draft_chars"] == len(_HEAD)
    assert payload["sections"] == ["Title", "Scope"]
    assert payload["valid"] is False
    # Missing-later-sections are diagnosable but never block staging.
    assert "SPEC008" in _diagnostic_rules(payload)


def test_stage_append_then_replace_all_controls_draft_content(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _HEAD}
    )
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _TAIL}
    )

    appended = _payload(handle_get_md_draft(session, workspace, {"artifact_type": "product_spec"}))
    assert appended["content"] == _HEAD + _TAIL
    assert appended["valid"] is True

    replaced = handle_stage_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": _HEAD, "mode": "replace_all"},
    )
    assert _payload(replaced)["draft_chars"] == len(_HEAD)


def test_stage_rejects_unknown_mode(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError):
        handle_stage_md_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {"artifact_type": "product_spec", "content": _HEAD, "mode": "prepend"},
        )


def test_stage_enforces_the_draft_size_cap_and_keeps_the_prior_draft(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _HEAD}
    )

    with pytest.raises(InvalidParamsError, match="character cap"):
        handle_stage_md_artifact(
            session,
            workspace,
            {"artifact_type": "product_spec", "content": "x" * 4_000_001},
        )

    kept = _payload(handle_get_md_draft(session, workspace, {"artifact_type": "product_spec"}))
    assert kept["content"] == _HEAD


def test_get_md_draft_survives_a_fresh_handler_process(tmp_path: Path) -> None:
    """The draft is persisted under the run's artifact area, not in memory."""
    handle_stage_md_artifact(
        MockSession(), MockWorkspace(tmp_path), {"artifact_type": "product_spec", "content": _HEAD}
    )

    # Fresh session/workspace objects model a restarted MCP server process.
    resumed = handle_get_md_draft(
        MockSession(), MockWorkspace(tmp_path), {"artifact_type": "product_spec"}
    )

    payload = _payload(resumed)
    assert payload["exists"] is True
    assert payload["content"] == _HEAD
    assert payload["sections"] == ["Title", "Scope"]


def test_get_md_draft_reports_absence_without_error(tmp_path: Path) -> None:
    result = handle_get_md_draft(
        MockSession(), MockWorkspace(tmp_path), {"artifact_type": "product_spec"}
    )

    assert result.is_error is False
    payload = _payload(result)
    assert payload["exists"] is False
    assert payload["content"] == ""


def test_discard_md_draft_drops_the_draft(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _HEAD}
    )

    discarded = handle_discard_md_draft(session, workspace, {"artifact_type": "product_spec"})
    rediscarded = handle_discard_md_draft(session, workspace, {"artifact_type": "product_spec"})

    assert _payload(discarded)["discarded"] is True
    assert _payload(rediscarded)["discarded"] is False
    after = _payload(handle_get_md_draft(session, workspace, {"artifact_type": "product_spec"}))
    assert after["exists"] is False


def test_finalize_submits_through_the_canonical_path_and_clears_the_draft(
    tmp_path: Path,
) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _HEAD}
    )
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _TAIL}
    )

    finalized = handle_finalize_md_artifact(session, workspace, {"artifact_type": "product_spec"})

    assert finalized.is_error is False
    verified = handle_verify_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _HEAD + _TAIL}
    )
    assert _payload(finalized) == _payload(verified)
    artifact_path = tmp_path / ".agent" / "artifacts" / "product_spec.md"
    assert artifact_path.read_text(encoding="utf-8") == _HEAD + _TAIL
    after = _payload(handle_get_md_draft(session, workspace, {"artifact_type": "product_spec"}))
    assert after["exists"] is False


def test_finalize_rejects_an_invalid_draft_and_keeps_it_for_repair(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _HEAD}
    )

    finalized = handle_finalize_md_artifact(session, workspace, {"artifact_type": "product_spec"})

    assert finalized.is_error is True
    verified = handle_verify_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _HEAD}
    )
    assert _payload(finalized) == _payload(verified)
    assert not (tmp_path / ".agent" / "artifacts" / "product_spec.md").exists()
    kept = _payload(handle_get_md_draft(session, workspace, {"artifact_type": "product_spec"}))
    assert kept["exists"] is True
    assert kept["content"] == _HEAD


def test_finalize_without_a_draft_is_an_invalid_params_error(tmp_path: Path) -> None:
    with pytest.raises(InvalidParamsError, match="no staged draft"):
        handle_finalize_md_artifact(
            MockSession(), MockWorkspace(tmp_path), {"artifact_type": "product_spec"}
        )


def test_staging_tools_are_registered_with_side_effect_contracts() -> None:
    tool_names = {spec.metadata.definition.name for spec in tool_specs(McpConfig())}

    assert {
        STAGE_MD_ARTIFACT_TOOL,
        GET_MD_DRAFT_TOOL,
        DISCARD_MD_DRAFT_TOOL,
        FINALIZE_MD_ARTIFACT_TOOL,
    } <= tool_names
    assert REGISTRY[STAGE_MD_ARTIFACT_TOOL].classification == "mutate"
    assert REGISTRY[GET_MD_DRAFT_TOOL].classification == "read"
    assert REGISTRY[DISCARD_MD_DRAFT_TOOL].classification == "mutate"
    assert REGISTRY[FINALIZE_MD_ARTIFACT_TOOL].classification == "mutate"
