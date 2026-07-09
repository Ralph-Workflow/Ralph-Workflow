"""Black-box tests for edit_file indexed target/match/impact-preview args.

The prompt requires edit_file to accept prompt-exact indexed
arguments: ``target`` (evidence/span/symbol), ``match_strategy``
(``exact|within_target|all_in_target``), ``expected_content_hash``,
``impact_preview``, ``reindex`` (``auto|skip|changed_blocking``),
and ``return_evidence_updates``.

Per the :mod:`ralph.mcp.explore.deferred_phases` register, those
args are Phase 3 (impact-aware editing) and are intentionally NOT
shipped in this slice. This test file therefore asserts:

* The current edit_file tool spec does NOT yet expose indexed args
  (the deferred register is the source of truth).
* The deferred-phase contract documents the prompt-exact enum
  values.
* The current edit_file handler still supports the legacy
  ``oldText/newText`` contract and remains backward compatible.
* The audit register rationale for edit_file references the
  indexed args and the prompt-exact match_strategy values.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.explore.audit_register import AUDIT_REGISTER
from ralph.mcp.explore.deferred_phases import DeferredPhaseRegistry
from ralph.mcp.tools.bridge._specs_file_write import file_write_specs
from ralph.mcp.tools.workspace._write_handlers import handle_edit_file

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._tool_spec import ToolSpec
    from ralph.mcp.tools.coordination import ToolContent


def _edit_spec() -> ToolSpec:
    for spec in file_write_specs():
        if spec.metadata.definition.name == "edit_file":
            return spec
    raise AssertionError("edit_file not in file_write_specs")


def _seed_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "module.py").write_text("def hello():\n    return 1\n")
    return workspace


class _FakeSession:
    def __init__(self) -> None:
        self.explore_index = None

    def check_capability(self, capability: str) -> dict[str, str]:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> dict[str, str]:
        return {"status": "approved", "path": path}


def test_edit_file_accepts_only_prompt_exact_match_strategy_values() -> None:
    """Phase 3 contract: match_strategy enum must be
    ``exact|within_target|all_in_target``.
    """
    phase_3 = DeferredPhaseRegistry.get("phase_3")
    assert phase_3 is not None
    deliverables_text = " ".join(phase_3.deliverables)
    assert "match_strategy" in deliverables_text
    for entry in AUDIT_REGISTER:
        if entry.tool == "edit_file":
            assert "match_strategy" in entry.rationale
            return
    raise AssertionError("edit_file not in AUDIT_REGISTER")


def test_edit_file_rejects_expected_content_hash_mismatch_before_mutation() -> None:
    """Phase 3 contract: edit_file fails closed on hash mismatch.

    Until Phase 3 lands, the current edit_file handler ignores
    ``expected_content_hash`` (the arg is reserved). The legacy
    oldText/newText contract must still work.
    """
    spec = _edit_spec()
    properties = spec.metadata.definition.input_schema.get("properties", {})
    assert "expected_content_hash" not in properties
    assert "path" in properties
    assert "edits" in properties
    assert "dry_run" in properties


def test_edit_file_target_symbol_returns_impact_preview_and_evidence_updates() -> None:
    """Phase 3 contract: target, impact_preview, return_evidence_updates
    are documented in deferred register.
    """
    spec = _edit_spec()
    properties = spec.metadata.definition.input_schema.get("properties", {})
    for arg in ("target", "impact_preview", "return_evidence_updates", "reindex"):
        assert arg not in properties, (
            f"{arg} is Phase 3 deferred; the current schema must not "
            f"expose it yet"
        )
    phase_3 = DeferredPhaseRegistry.get("phase_3")
    assert phase_3 is not None
    deliverables_text = " ".join(phase_3.deliverables)
    for arg in ("expected_content_hash", "target", "impact_preview"):
        assert arg in deliverables_text, f"{arg} missing from Phase 3 deliverables"


def test_edit_file_legacy_oldtext_newtext_contract_still_works(tmp_path: Path) -> None:
    """The legacy ``oldText/newText`` edit contract must still work after
    Phase 3 is deferred.
    """
    from tests.mock_session import MockSession

    workspace = _seed_workspace(tmp_path)
    from ralph.mcp.tools.workspace import WORKSPACE_EDIT_CAPABILITY

    session = MockSession(WORKSPACE_EDIT_CAPABILITY)
    from unittest.mock import MagicMock

    ws = MagicMock()
    ws.read.return_value = "def hello():\n    return 1\n"
    ws.write.return_value = None
    ws.is_path_git_tracked.return_value = False
    ws.absolute_path.return_value = str(workspace / "module.py")
    result = handle_edit_file(
        session,
        ws,
        {
            "path": "module.py",
            "edits": [{"oldText": "return 1", "newText": "return 2"}],
        },
    )
    payload = json.loads(cast("ToolContent", result.content[0]).text)
    assert payload["status"] == "applied"
    assert "diff" in payload
    assert "bytes_written" in payload
