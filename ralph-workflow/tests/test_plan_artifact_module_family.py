"""Regression tests for the plan-artifact module family.

The original ``ralph.mcp.artifacts.plan.__init__`` was 1105 lines of
helpers. The refactor splits that surface into seven focused submodules
and turns ``__init__`` into a thin re-export surface. These tests
guard the boundary:

  - ``test_all_public_symbols_still_importable``: every name in the
    package ``__all__`` must be importable.
  - ``test_submodule_owners``: each submodule owns at least one
    public symbol so a cheap model can locate helpers by owner.
  - ``test_no_circular_imports``: every submodule can be imported in
    isolation without a circular-import error.
  - ``test_parse_plan_payload_envelope_aware``: both strict and
    lenient decoders round-trip envelope-wrapped and bare-dict
    payloads (the consolidated parser core).
"""

from __future__ import annotations

import importlib
import inspect

import pytest

import ralph.mcp.artifacts.plan as plan_pkg
from ralph.mcp.artifacts.plan._validation import (
    parse_plan_payload_lenient,
    parse_plan_payload_strict,
)

SUBMODULES: tuple[str, ...] = (
    "_section_models",
    "_section_registry",
    "_validation",
    "_step_edit",
    "_renderers",
    "_draft_io",
    "_step_contract",
    "_noop",
    "_plan_step",
)

OWNED_SYMBOLS: dict[str, tuple[str, ...]] = {
    "_plan_step": ("PlanStep",),
    "_section_registry": ("PLAN_ARTIFACT_TYPE", "SectionMode", "PLAN_SECTION_NAMES"),
    "_renderers": ("render_plan_markdown", "extract_plan_payload"),
    "_draft_io": ("load_plan_draft", "new_plan_draft", "save_plan_draft"),
    "_step_contract": ("StepType", "requires_targets", "requires_verify_handle"),
    "_noop": ("PlanArtifactValidationError", "is_noop_plan"),
    "_validation": ("PlanArtifact", "parse_plan_payload_strict"),
}


@pytest.mark.parametrize("submodule_name", SUBMODULES)
def test_submodule_imports_in_isolation(submodule_name: str) -> None:
    mod = importlib.import_module(f"ralph.mcp.artifacts.plan.{submodule_name}")
    assert inspect.ismodule(mod)


def test_all_public_symbols_still_importable() -> None:
    names = set(plan_pkg.__all__)
    assert len(names) >= 45, f"expected >=45 public symbols, got {len(names)}"
    missing = [n for n in names if not hasattr(plan_pkg, n)]
    assert not missing, f"public symbols missing from package: {missing}"


@pytest.mark.parametrize(
    "submodule_name,expected",
    list(OWNED_SYMBOLS.items()),
)
def test_submodule_owners(submodule_name: str, expected: tuple[str, ...]) -> None:
    mod = importlib.import_module(f"ralph.mcp.artifacts.plan.{submodule_name}")
    for name in expected:
        assert hasattr(mod, name), (
            f"submodule {submodule_name!r} is expected to own {name!r}"
        )


def test_parse_plan_payload_envelope_aware() -> None:
    bare = {"summary": {"intent": "x"}, "steps": []}
    envelope = {"type": "plan", "content": bare}

    assert parse_plan_payload_strict(bare) == bare
    assert parse_plan_payload_lenient(bare) == bare
    assert parse_plan_payload_strict(envelope) == bare
    assert parse_plan_payload_lenient(envelope) == bare
    assert parse_plan_payload_lenient("{not json") is None
