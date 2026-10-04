"""Read-only validation coverage for staged plan markdown."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic import TypeAdapter

from ralph.mcp.tools.md_artifact import (
    REPAIR_HINT,
    handle_get_md_draft,
    handle_stage_md_artifact,
    handle_submit_md_artifact,
    handle_verify_md_artifact,
)
from ralph.mcp.tools.tool_content import ToolContent
from ralph.policy import loader as policy_loader
from ralph.policy.models import (
    PhaseDefinition,
    PhaseParallelization,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace
from tests._artifact_format_docs_mock_session import planning_session
from tests._support.typed_accessors import (
    must_dict_list,
)
from tests.mcp.test_md_plan_spec import _plan_document

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.mcp.tools.coordination_session_like import CoordinationSessionLike
    from ralph.mcp.tools.tool_result import ToolResult
    from ralph.policy.models import PolicyBundle

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


def test_get_draft_accepts_dangling_dependency_without_diagnostics(
    tmp_path: Path,
) -> None:
    """A dangling dependency is best-effort, not a blocking draft diagnostic.

    The new contract is sanity-only: structural problems are absorbed
    silently. Draft state stays consistent across reads.
    """
    workspace = FsWorkspace(tmp_path)
    invalid = _plan_document().replace("Depends on: S-1", "Depends on: S-99")
    handle_stage_md_artifact(
        _session(),
        workspace,
        {"artifact_type": "plan", "content": invalid},
    )

    first = _payload(handle_get_md_draft(_session(), workspace, {"artifact_type": "plan"}))
    second = _payload(handle_get_md_draft(_session(), workspace, {"artifact_type": "plan"}))

    assert first["valid"] is True
    assert not any(item["rule_id"] == "PLAN021" for item in first["diagnostics"])
    assert first["content"] == invalid
    assert second["content"] == invalid
    assert second["diagnostics"] == first["diagnostics"]


# === Reserved-path ownership is a downstream concern, not a plan-validate concern.
def test_verify_accepts_reserved_path_unit_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan with a reserved-path unit is accepted; the development phase
    drops reserved paths from the unit's brief."""
    from ralph.policy.models import PhaseParallelization

    parallelization = PhaseParallelization(max_parallel_workers=2)
    bundle = _make_bundle_with_development_parallelization(
        tmp_path, parallelization=parallelization
    )
    monkeypatch.setattr(policy_loader, "load_policy", lambda _config_dir: bundle)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units([("u1", ".agent")])

    result = handle_verify_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    payload = _payload(result)
    assert payload["valid"] is True
    diagnostics = must_dict_list(payload["diagnostics"])
    assert not any(d["rule_id"] == "WUPOL001" for d in diagnostics)


def test_verify_accepts_overlapping_responsibility_areas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ralph.policy.models import PhaseParallelization

    parallelization = PhaseParallelization(max_parallel_workers=2)
    bundle = _make_bundle_with_development_parallelization(
        tmp_path, parallelization=parallelization
    )
    monkeypatch.setattr(policy_loader, "load_policy", lambda _config_dir: bundle)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units([("u1", "src"), ("u2", "src/sub")])

    result = handle_verify_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    payload = _payload(result)
    assert payload["valid"] is True


def test_verify_accepts_work_units_exceeding_max_work_units_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Plan with 4 units and ``max_work_units=3`` is accepted; the cap is
    a worker concurrency concern, not a plan-validation one."""
    from ralph.policy.models import PhaseParallelization

    parallelization = PhaseParallelization(
        max_parallel_workers=8,
        max_work_units=3,
    )
    bundle = _make_bundle_with_development_parallelization(
        tmp_path, parallelization=parallelization
    )
    monkeypatch.setattr(policy_loader, "load_policy", lambda _config_dir: bundle)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units(
        [(f"u{i}", f"dir{i}") for i in range(4)]
    )

    result = handle_verify_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    payload = _payload(result)
    assert payload["valid"] is True
    diagnostics = must_dict_list(payload["diagnostics"])
    assert not any(
        d["rule_id"] == "WUPOL001" and "max_work_units" in d["message"] for d in diagnostics
    )


def test_verify_passes_clean_work_units_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A clean two-unit plan with disjoint directories validates successfully."""
    from ralph.policy.models import PhaseParallelization

    parallelization = PhaseParallelization(max_parallel_workers=2)
    bundle = _make_bundle_with_development_parallelization(
        tmp_path, parallelization=parallelization
    )
    monkeypatch.setattr(policy_loader, "load_policy", lambda _config_dir: bundle)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units([("u1", "src"), ("u2", "tests")])

    result = handle_verify_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    payload = _payload(result)
    assert payload["valid"] is True
    diagnostics = must_dict_list(payload["diagnostics"])
    assert not any(d["rule_id"].startswith("WUPOL") for d in diagnostics)


def test_submit_accepts_work_units_and_persists_the_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A plan with a reserved-path unit is accepted and persisted; the
    development phase drops reserved paths from the unit's brief."""
    from ralph.policy.models import PhaseParallelization

    parallelization = PhaseParallelization(max_parallel_workers=2)
    bundle = _make_bundle_with_development_parallelization(
        tmp_path, parallelization=parallelization
    )
    monkeypatch.setattr(policy_loader, "load_policy", lambda _config_dir: bundle)

    workspace = FsWorkspace(tmp_path)
    content = _plan_with_work_units([("u1", ".agent")])

    submit_result = handle_submit_md_artifact(
        _session(), workspace, {"artifact_type": "plan", "content": content}
    )

    assert submit_result.is_error is False
    payload = _payload(submit_result)
    assert payload["valid"] is True
    diagnostics = must_dict_list(payload["diagnostics"])
    assert not any(d["rule_id"] == "WUPOL001" for d in diagnostics)


@pytest.mark.parametrize("section", ["Work Units", "Parallel Plan"])
def test_verify_ignores_policy_load_errors(
    monkeypatch: pytest.MonkeyPatch, section: str
) -> None:
    """A policy-load failure no longer blocks plan submission.

    The new contract is sanity-only. Effective-policy evaluation
    remains a downstream concern, not a plan-validation one.
    """
    def unavailable_policy(_config_dir: Path) -> PolicyBundle:
        raise OSError("policy is unreadable")

    monkeypatch.setattr(policy_loader, "load_policy", unavailable_policy)
    content = _plan_with_work_units([("U-1", "src")]).replace("Work Units", section)
    payload = _payload(handle_verify_md_artifact(
        _session(), MemoryWorkspace(), {"artifact_type": "plan", "content": content}
    ))

    assert payload["valid"] is True
    diagnostics = must_dict_list(payload["diagnostics"])
    assert not any(
        item["rule_id"] == "WUPOL001" and "policy is unreadable" in item["message"]
        for item in diagnostics
    )


def test_verify_accepts_parallel_plan_reserved_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plan with a reserved-path Parallel Plan unit is accepted."""
    bundle = _make_bundle_with_development_parallelization(
        MemoryWorkspace().root, parallelization=PhaseParallelization()
    )
    monkeypatch.setattr(policy_loader, "load_policy", lambda _config_dir: bundle)
    content = _plan_with_work_units([("U-1", ".git")]).replace("Work Units", "Parallel Plan")
    payload = _payload(handle_verify_md_artifact(
        _session(), MemoryWorkspace(), {"artifact_type": "plan", "content": content}
    ))

    assert payload["valid"] is True
    assert not any(
        item["rule_id"] == "WUPOL001" and "reserved path" in item["message"]
        for item in must_dict_list(payload["diagnostics"])
    )


def _make_bundle_with_development_parallelization(
    workspace_root: Path,
    *,
    parallelization: PhaseParallelization,
) -> PolicyBundle:
    """Return a real ``PolicyBundle`` with the development phase's parallelization swapped.

    Loads the bundled default policy against a real ``.agent/`` directory (or
    an empty temp directory; the loader falls back to bundled defaults when
    policy files are absent) and replaces its pipeline with a minimal two-phase
    graph that carries the desired ``PhaseParallelization``. Tests then
    monkeypatch the public ``ralph.policy.loader.load_policy`` to return this
    bundle, satisfying the DA-011 contract that tests patch public seams
    rather than import private helpers.
    """
    real_bundle = policy_loader.load_policy(workspace_root / ".agent")
    synthetic_pipeline = PipelinePolicy(
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
    return real_bundle.model_copy(update={"pipeline": synthetic_pipeline})
