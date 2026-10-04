"""Read-only validation coverage for staged plan markdown."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import TypeAdapter

from ralph.mcp.tools import md_artifact as md_artifact_module
from ralph.mcp.tools.md_artifact import (
    REPAIR_HINT,
    handle_get_md_draft,
    handle_stage_md_artifact,
    handle_submit_md_artifact,
    handle_verify_md_artifact,
)
from ralph.mcp.tools.tool_content import ToolContent
from ralph.policy.models import (
    PhaseDefinition,
    PhaseParallelization,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.workspace.fs import FsWorkspace
from tests._artifact_format_docs_mock_session import planning_session
from tests._support.typed_accessors import (
    must_dict_list,
)
from tests.mcp.test_md_plan_spec import _plan_document

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

    from ralph.mcp.tools.coordination_session_like import CoordinationSessionLike
    from ralph.mcp.tools.tool_result import ToolResult

_JSON_OBJECT = TypeAdapter(dict[str, object])


def _session() -> CoordinationSessionLike:
    return planning_session()


def _payload(result: ToolResult) -> dict[str, object]:
    block = result.content[0]
    assert isinstance(block, ToolContent)
    return _JSON_OBJECT.validate_json(block.text)


def _plan_with_work_units(units: list[tuple[str, str]]) -> str:
    """Build a plan document whose only declared work unit entries are ``units``.

    Each entry is ``(unit_id, directories_comma_separated)``. The plan body
    satisfies the validator so work_units policy checks run end-to-end.
    """
    items = "\n".join(
        f"- [{uid}] {uid} body\n  Directories: {dirs}" for uid, dirs in units
    )
    return f"""---
type: plan
---
## Summary
Test plan with work units.

## Work
### [S-1] Make a change
Type: file_change
Files:
- modify src/example.py
Verify: pytest -q tests/example_test.py
Expect: focused tests pass with exit code 0

## Work Units
{items}
"""


def test_verify_complete_plan_is_valid_without_persisting_it(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)

    result = handle_verify_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": _plan_document()},
    )

    assert result.is_error is False
    assert _payload(result) == {
        "artifact_type": "plan",
        "valid": True,
        "diagnostics": [],
        "counts": {"error": 0, "info": 0, "warning": 0},
        "overridden": [],
        "repair_hint": REPAIR_HINT,
    }
    assert not (tmp_path / ".agent" / "artifacts" / "plan.md").exists()


def test_get_draft_reports_cross_reference_error_without_mutating_content(
    tmp_path: Path,
) -> None:
    """A dangling dependency remains visible as a blocking draft diagnostic."""
    workspace = FsWorkspace(tmp_path)
    invalid = _plan_document().replace("Depends on: S-1", "Depends on: S-99")
    handle_stage_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": invalid},
    )

    first = _payload(handle_get_md_draft(_session(), workspace, {"artifact_type": "plan"}))
    second = _payload(handle_get_md_draft(_session(), workspace, {"artifact_type": "plan"}))

    diagnostics = must_dict_list(first["diagnostics"])
    assert first["valid"] is False
    assert any(item["rule_id"] == "PLAN021" and item["severity"] == "error" for item in diagnostics)
    assert first["content"] == invalid
    assert second["content"] == invalid
    assert second["diagnostics"] == diagnostics


# === S-9: work_units policy diagnostics at plan verify/submit time ===
def test_verify_rejects_reserved_path_unit_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan whose only unit declares ``.agent`` must produce a line-anchored diagnostic.

    The singleton-hole fix in ``validate_work_units_against_policy`` ensures a
    single-unit plan pointing at ``.agent`` (or any reserved path) is
    rejected. The plan-verify gate surfaces that as a ``WUPOL001`` diagnostic
    anchored to the ``## Work Units`` section, blocking submission.
    """
    from ralph.policy.models import PhaseParallelization

    policy = _minimal_pipeline_policy_with_parallelization(
        parallelization=PhaseParallelization(max_parallel_workers=2)
    )
    monkeypatch.setattr(md_artifact_module, "_load_policy_pipeline", lambda _root: policy)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units([("u1", ".agent")])

    result = handle_verify_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    payload = _payload(result)
    assert payload["valid"] is False
    diagnostics = must_dict_list(payload["diagnostics"])
    reserved = [d for d in diagnostics if d["rule_id"] == "WUPOL001"]
    assert reserved, f"expected a WUPOL001 diagnostic, got {diagnostics}"
    assert reserved[0]["section"] == "Work Units"
    assert "reserved path" in reserved[0]["message"]


def test_verify_rejects_overlapping_unit_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two units with prefix-overlapping directories fail verify at submit time."""
    from ralph.policy.models import PhaseParallelization

    policy = _minimal_pipeline_policy_with_parallelization(
        parallelization=PhaseParallelization(max_parallel_workers=2)
    )
    monkeypatch.setattr(md_artifact_module, "_load_policy_pipeline", lambda _root: policy)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units([("u1", "src"), ("u2", "src/sub")])

    result = handle_verify_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    payload = _payload(result)
    assert payload["valid"] is False
    diagnostics = must_dict_list(payload["diagnostics"])
    assert any(
        d["rule_id"] == "WUPOL001" and "overlaps" in d["message"] for d in diagnostics
    ), f"expected a WUPOL001 overlap diagnostic, got {diagnostics}"


def test_verify_rejects_work_units_exceeding_max_work_units_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan with 4 units and ``max_work_units=3`` is rejected at verify.

    Each diagnostic names the violated limit and its value (per the S-9
    plan's "name the two limits distinctly" requirement): here
    ``max_work_units=3``.
    """
    from ralph.policy.models import PhaseParallelization

    policy = _minimal_pipeline_policy_with_parallelization(
        parallelization=PhaseParallelization(
            max_parallel_workers=8,
            max_work_units=3,
        )
    )
    monkeypatch.setattr(md_artifact_module, "_load_policy_pipeline", lambda _root: policy)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units(
        [(f"u{i}", f"dir{i}") for i in range(4)]
    )

    result = handle_verify_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    payload = _payload(result)
    assert payload["valid"] is False
    diagnostics = must_dict_list(payload["diagnostics"])
    assert any(
        d["rule_id"] == "WUPOL001" and "max_work_units" in d["message"] for d in diagnostics
    ), f"expected max_work_units-named diagnostic, got {diagnostics}"


def test_verify_passes_clean_work_units_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean two-unit plan with disjoint directories validates successfully."""
    from ralph.policy.models import PhaseParallelization

    policy = _minimal_pipeline_policy_with_parallelization(
        parallelization=PhaseParallelization(max_parallel_workers=2)
    )
    monkeypatch.setattr(md_artifact_module, "_load_policy_pipeline", lambda _root: policy)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units([("u1", "src"), ("u2", "tests")])

    result = handle_verify_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    payload = _payload(result)
    assert payload["valid"] is True
    diagnostics = must_dict_list(payload["diagnostics"])
    assert not any(d["rule_id"].startswith("WUPOL") for d in diagnostics)


def test_submit_blocks_work_units_violations_and_keeps_draft_staged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Submit returns ``is_error=True`` and leaves the draft staged for repair."""
    from ralph.policy.models import PhaseParallelization

    policy = _minimal_pipeline_policy_with_parallelization(
        parallelization=PhaseParallelization(max_parallel_workers=2)
    )
    monkeypatch.setattr(md_artifact_module, "_load_policy_pipeline", lambda _root: policy)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units([("u1", ".agent")])

    submit_result = handle_submit_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    assert submit_result.is_error is True
    payload = _payload(submit_result)
    assert payload["valid"] is False
    diagnostics = must_dict_list(payload["diagnostics"])
    assert any(
        d["rule_id"] == "WUPOL001" and "reserved path" in d["message"] for d in diagnostics
    )

    draft_path = tmp_path / ".agent" / "artifacts" / ".plan.draft.md"
    assert draft_path.exists(), "submit must keep the rejected draft staged for repair"

    follow_up = handle_get_md_draft(_session(), workspace, {"artifact_type": "plan"})
    follow_up_payload = _payload(follow_up)
    assert follow_up_payload["content"] == content
    assert follow_up_payload["exists"] is True


def _minimal_pipeline_policy_with_parallelization(
    *,
    parallelization: PhaseParallelization,
) -> PipelinePolicy:
    """Return a minimal two-phase ``PipelinePolicy`` with the given development parallelization."""
    return PipelinePolicy(
        entry_phase="development",
        terminal_phase="complete",
        phases={
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="complete"),
                parallelization=parallelization,
            ),
            "complete": PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
    )
