"""Structured logging coverage for markdown artifact validation outcomes."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import TypeAdapter

from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.md_artifact import (
    handle_edit_md_artifact,
    handle_finalize_md_artifact,
    handle_stage_md_artifact,
    handle_submit_md_artifact,
    handle_verify_md_artifact,
)
from tests._artifact_format_docs_memory_backend import MemoryBackend
from tests._support.typed_accessors import must_text
from tests._tool_artifact_2_helper_mocksession import MockSession
from tests._tool_artifact_2_helper_mockworkspace import MockWorkspace

if TYPE_CHECKING:
    from ralph.mcp.tools.coordination import ToolResult

_INVALID = "---\ntype: product_spec\n---\n"
_VALID = """---
type: product_spec
---
## Title
- [T1] Logging
## Scope
- [S1] Scope
## Goals
- [G1] Goals
## Users
- [U1] Users
## Success Criteria
- [C1] Criteria
"""


_JSON_OBJECT = TypeAdapter(dict[str, object])


def _payload(result: ToolResult) -> dict[str, object]:
    return _JSON_OBJECT.validate_json(must_text(result.content[0]))


def _capture() -> tuple[list[str], int]:
    captured: list[str] = []
    sink_id = logger.add(captured.append, format="{message}")
    return captured, sink_id


def test_rejected_submit_logs_every_error_diagnostic_with_bounded_fields(tmp_path: Path) -> None:
    captured, sink_id = _capture()
    try:
        result = handle_submit_md_artifact(
            MockSession(),
            MockWorkspace(tmp_path),
            {"artifact_type": "product_spec", "content": _INVALID},
        )
    finally:
        logger.remove(sink_id)

    assert result.is_error is True
    assert len(captured) == 1
    message = captured[0]
    assert "artifact_type=product_spec" in message
    assert "rule_id=SPEC008" in message
    assert "line=1" in message
    assert "section=Goals" in message
    assert "message=" in message
    assert len(message) <= 2000


def test_applied_invalid_edit_logs_rejection(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)
    handle_stage_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": _INVALID, "mode": "replace_all"},
        deps=deps,
    )

    captured, sink_id = _capture()
    try:
        result = handle_edit_md_artifact(
            session,
            workspace,
            {"artifact_type": "product_spec", "edits": [{"oldText": "---", "newText": "---"}]},
            deps=deps,
        )
    finally:
        logger.remove(sink_id)

    assert result.is_error is True
    assert len(captured) == 1
    assert "artifact_type=product_spec" in captured[0]
    assert "rule_id=SPEC008" in captured[0]


def test_rejected_finalize_logs_validation_diagnostic(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    deps = ArtifactHandlerDeps(backend=MemoryBackend())
    handle_stage_md_artifact(
        session,
        workspace,
        {"artifact_type": "product_spec", "content": _INVALID, "mode": "replace_all"},
        deps=deps,
    )

    captured, sink_id = _capture()
    try:
        result = handle_finalize_md_artifact(
            session,
            workspace,
            {"artifact_type": "product_spec"},
            deps=deps,
        )
    finally:
        logger.remove(sink_id)

    assert result.is_error is True
    assert len(captured) == 1
    message = captured[0]
    assert "artifact_type=product_spec" in message
    assert "rule_id=SPEC008" in message
    assert "line=1" in message
    assert "section=Goals" in message
    assert "message=" in message
    assert len(message) <= 2000


def test_recovered_submit_and_edit_log_info_but_non_recovered_paths_do_not(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)
    handle_submit_md_artifact(session, workspace, {"artifact_type": "product_spec", "content": _INVALID}, deps=deps)

    captured, sink_id = _capture()
    try:
        recovered_submit = handle_submit_md_artifact(
            session, workspace, {"artifact_type": "product_spec", "content": _VALID}, deps=deps
        )
    finally:
        logger.remove(sink_id)
    assert _payload(recovered_submit)["validation_recovered"] is True
    assert any(record.strip() == "VALIDATION RECOVERED artifact_type=product_spec" for record in captured)

    captured, sink_id = _capture()
    try:
        normal_submit = handle_submit_md_artifact(
            MockSession(), MockWorkspace(tmp_path / "normal"), {"artifact_type": "product_spec", "content": _VALID}, deps=ArtifactHandlerDeps(backend=MemoryBackend())
        )
    finally:
        logger.remove(sink_id)
    assert normal_submit.is_error is False
    assert not any("artifact_type=product_spec" in record for record in captured)

    captured, sink_id = _capture()
    try:
        verified = handle_verify_md_artifact(
            session, workspace, {"artifact_type": "product_spec", "content": _VALID}
        )
    finally:
        logger.remove(sink_id)
    assert verified.is_error is False
    assert captured == []

    captured, sink_id = _capture()
    try:
        preview = handle_edit_md_artifact(
            session,
            workspace,
            {"artifact_type": "product_spec", "edits": [{"oldText": "---", "newText": "---"}], "dry_run": True},
            deps=deps,
        )
    finally:
        logger.remove(sink_id)
    assert preview.is_error is False
    assert captured == []


def test_recovered_edit_logs_info(tmp_path: Path) -> None:
    session = MockSession()
    workspace = MockWorkspace(tmp_path)
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "product_spec", "content": _INVALID, "mode": "replace_all"}, deps=deps
    )
    handle_submit_md_artifact(session, workspace, {"artifact_type": "product_spec", "content": _INVALID}, deps=deps)

    captured, sink_id = _capture()
    try:
        result = handle_edit_md_artifact(
            session, workspace, {"artifact_type": "product_spec", "edits": [{"oldText": _INVALID, "newText": _VALID}]}, deps=deps
        )
    finally:
        logger.remove(sink_id)

    assert _payload(result)["validation_recovered"] is True
    assert any(record.strip() == "VALIDATION RECOVERED artifact_type=product_spec" for record in captured)
