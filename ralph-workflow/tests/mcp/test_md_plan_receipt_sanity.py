"""Receipt-backed plan acceptance through the public artifact tools."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.artifacts.canonical_submit import promote_fallback_artifact
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.tools import artifact
from ralph.mcp.tools.artifact import ArtifactHandlerDeps
from ralph.mcp.tools.md_artifact import (
    handle_edit_md_artifact,
    handle_finalize_md_artifact,
    handle_stage_md_artifact,
    handle_submit_md_artifact,
    handle_verify_md_artifact,
)
from ralph.workspace.memory import MemoryWorkspace
from tests._tool_artifact_2_helper_memorybackend import MemoryBackend
from tests._tool_artifact_2_helper_mocksession import MockSession

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def unavailable_history_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_path: Path) -> None:
        raise OSError("No optional history policy in this memory workspace")

    monkeypatch.setattr(artifact, "load_policy", unavailable)


class Utf8Backend(MemoryBackend):
    """Mirror the real UTF-8 writer's encoding boundary without filesystem I/O."""

    def write_text(self, path: Path, content: str, *, encoding: str = "utf-8") -> None:
        content.encode(encoding)
        super().write_text(path, content, encoding=encoding)


_PROSE = "Inspect code then implement independent changes and verify all behavior carefully."
_CASES = (
    "Inspect code then implement changes and verify all behavior carefully.",
    _PROSE,
    _PROSE + "\n## Steps\n### [S-1] First\n### [S-1] Second",
    _PROSE + "\n## Steps\n### [S-1] First\nDepends on: S-99",
    _PROSE + "\n## Steps\n### [S-1] First\nDepends on: S-2\n### [S-2] Second\nDepends on: S-1",
    _PROSE + "\n## Work Units\n- [U-1] First\n  Directories: src\n- [U-2] Second\n  Directories: src",
    _PROSE + "\n## Work Units\n- [U-1] First\n- [U-2] Second",
    _PROSE + "\n## Work Units\n" + "\n".join(f"- [U-{i}] Work on file {i}" for i in range(1, 66)),
    _PROSE + "\n## Work Units\n- [U-1] First\n## Parallel Plan\n- [U-2] Second",
    _PROSE + "\n## Steps\n### [S-1] Change the implementation",
    "---\ntype: plan\ntype: other\nbroken metadata\n---\n" + _PROSE,
)


@pytest.mark.parametrize("document", _CASES)
def test_permissive_plan_receives_receipt_without_structural_diagnostics(document: str) -> None:
    workspace = MemoryWorkspace()
    session = MockSession("planning")
    session.run_id = "receipt-matrix"
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend, receipt_secret=None)
    params: dict[str, object] = {"artifact_type": "plan", "content": document}

    verified = handle_verify_md_artifact(session, workspace, params)
    submitted = handle_submit_md_artifact(session, workspace, params, deps=deps)

    for result in (verified, submitted):
        assert result.is_error is False
        assert json.loads(result.content[0].text)["diagnostics"] == []
    assert backend.read_text(workspace.root / ".agent/artifacts/plan.md") == document
    assert backend.read_text(workspace.root / ".agent/PLAN.md") == document
    assert artifact_receipt_present(
        workspace.root, session.run_id, "plan", backend=backend, receipt_secret=None
    )


def test_stage_finalize_edit_and_fallback_preserve_prose_and_receipts() -> None:
    workspace = MemoryWorkspace()
    session = MockSession("planning")
    session.run_id = "draft-parity"
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend, receipt_secret=None)
    handle_stage_md_artifact(
        session, workspace, {"artifact_type": "plan", "content": _PROSE}, deps=deps
    )
    assert not handle_finalize_md_artifact(
        session, workspace, {"artifact_type": "plan"}, deps=deps
    ).is_error
    edited = _PROSE + "\n## Steps\n### [S-1] First\nDepends on: S-999"
    result = handle_edit_md_artifact(
        session, workspace,
        {"artifact_type": "plan", "edits": [{"oldText": _PROSE, "newText": edited}]},
        deps=deps,
    )
    assert not result.is_error
    assert json.loads(result.content[0].text)["submitted"] is True
    assert backend.read_text(workspace.root / ".agent/PLAN.md") == edited
    assert artifact_receipt_present(
        workspace.root, session.run_id, "plan", backend=backend, receipt_secret=None
    )

    fallback = workspace.root / ".agent/tmp/plan.md"
    backend.write_text(fallback, _PROSE)
    promoted = promote_fallback_artifact(
        workspace.root, "plan", deps=deps, run_id="fallback-parity"
    )
    assert promoted is not None
    assert not backend.exists(fallback)
    assert backend.read_text(workspace.root / ".agent/PLAN.md") == _PROSE
    assert artifact_receipt_present(
        workspace.root, "fallback-parity", "plan", backend=backend, receipt_secret=None
    )


@pytest.mark.parametrize(
    "document",
    ("", "one two three four five six seven eight nine", "\x00ID3" + _PROSE,
     "\ud800" + _PROSE, "I cannot complete this request because policy prevents me from helping you today."),
)
def test_bad_plan_never_receives_submit_or_fallback_receipt(document: str) -> None:
    workspace = MemoryWorkspace()
    session = MockSession("planning")
    session.run_id = "bad-plan"
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend, receipt_secret=None)
    params: dict[str, object] = {"artifact_type": "plan", "content": document}
    assert handle_verify_md_artifact(session, workspace, params).is_error
    assert handle_submit_md_artifact(session, workspace, params, deps=deps).is_error
    fallback = workspace.root / ".agent/tmp/plan.md"
    backend.write_text(fallback, document)
    assert promote_fallback_artifact(
        workspace.root, "plan", deps=deps, run_id=session.run_id
    ) is None
    assert not artifact_receipt_present(
        workspace.root, session.run_id, "plan", backend=backend, receipt_secret=None
    )
    assert not backend.exists(workspace.root / ".agent/PLAN.md")


@pytest.mark.parametrize("operation", ["submit", "stage", "edit"])
def test_unencodable_plan_returns_sanity_diagnostic_before_persistence(operation: str) -> None:
    workspace = MemoryWorkspace()
    session = MockSession("planning")
    deps = ArtifactHandlerDeps(backend=Utf8Backend())
    if operation == "edit":
        handle_stage_md_artifact(
            session, workspace, {"artifact_type": "plan", "content": _PROSE}, deps=deps
        )
        result = handle_edit_md_artifact(
            session, workspace,
            {"artifact_type": "plan", "edits": [{"oldText": _PROSE, "newText": "\ud800" + _PROSE}]},
            deps=deps,
        )
    else:
        handler = handle_submit_md_artifact if operation == "submit" else handle_stage_md_artifact
        result = handler(
            session, workspace,
            {"artifact_type": "plan", "content": "\ud800" + _PROSE},
            deps=deps,
        )
    assert result.is_error
    assert json.loads(result.content[0].text)["diagnostics"][0]["rule_id"] == "PLAN001"


def test_prose_plan_requires_exactly_one_plan_level_development_proof() -> None:
    workspace = MemoryWorkspace()
    session = MockSession()
    session.run_id = "proof-parity"
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)
    assert not handle_submit_md_artifact(
        session, workspace, {"artifact_type": "plan", "content": _PROSE}, deps=deps
    ).is_error
    development = """---
type: development_result
status: completed
---
## Summary
- [SUM-1] Implemented the requested repository changes.
## Files Changed
- [F-1] src/example.py
## Plan Items Proven
- [plan] Ran the focused verification and observed all assertions passing.
  Disposition: completed
"""
    bad = handle_submit_md_artifact(
        session, workspace,
        {"artifact_type": "development_result", "content": development.replace("[plan]", "[S-999]")},
        deps=deps,
    )
    assert bad.is_error
    assert any(item["rule_id"] == "DEV015" for item in json.loads(bad.content[0].text)["diagnostics"])
    good = handle_submit_md_artifact(
        session, workspace, {"artifact_type": "development_result", "content": development},
        deps=deps,
    )
    assert not good.is_error
    assert artifact_receipt_present(
        workspace.root, session.run_id, "development_result", backend=backend
    )


def test_oversized_plan_returns_same_sanity_failure_without_a_receipt() -> None:
    workspace = MemoryWorkspace()
    session = MockSession("planning")
    session.run_id = "oversized"
    backend = MemoryBackend()
    deps = ArtifactHandlerDeps(backend=backend)
    document = _PROSE + ("界" * 1_333_334)
    assert len(document) < 4_000_000 < len(document.encode("utf-8"))
    params: dict[str, object] = {"artifact_type": "plan", "content": document}
    verified = handle_verify_md_artifact(session, workspace, params)
    submitted = handle_submit_md_artifact(session, workspace, params, deps=deps)
    assert verified.is_error and submitted.is_error
    assert json.loads(verified.content[0].text)["diagnostics"] == json.loads(
        submitted.content[0].text
    )["diagnostics"]
    fallback = workspace.root / ".agent/tmp/plan.md"
    backend.write_text(fallback, document)
    assert promote_fallback_artifact(workspace.root, "plan", deps=deps, run_id=session.run_id) is None
    assert not artifact_receipt_present(workspace.root, session.run_id, "plan", backend=backend)


def test_submit_regression_accepts_oversized_numeric_step_token() -> None:
    workspace = MemoryWorkspace()
    session = MockSession("planning")
    backend = MemoryBackend()
    # Use a real parent section so the parser actually attaches the block to a
    # section; without that, extraction is a no-op and the int() overflow
    # path is never exercised. The oversized step ID lives inside the section
    # where the plan mapper would convert it to an int.
    document = _PROSE + "\n\n## Steps\n\n### [S-" + ("9" * 4_301) + "] Structural token remains prose"

    result = handle_submit_md_artifact(
        session,
        workspace,
        {"artifact_type": "plan", "content": document},
        deps=ArtifactHandlerDeps(backend=backend),
    )

    assert not result.is_error
    assert json.loads(result.content[0].text)["diagnostics"] == []
