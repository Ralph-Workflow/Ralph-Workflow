"""Regression tests for the plan-artifact module family.

The original ``ralph.mcp.artifacts.plan.__init__`` was 1105 lines of
helpers. The refactor splits that surface into focused submodules
and turns ``__init__`` into a thin re-export surface. These tests
guard the boundary:

  - ``test_all_public_symbols_still_importable``: every name in the
    package ``__all__`` must be importable.
  - ``test_submodule_owners``: each submodule owns at least one
    public symbol so a cheap model can locate helpers by owner.
  - ``test_no_circular_imports``: every submodule can be imported in
    isolation without a circular-import error.
"""

from __future__ import annotations

import importlib
import inspect

import pytest

import ralph.mcp.artifacts.plan as plan_pkg
from ralph.mcp.artifacts.plan._size_limits import PlanSizeLimits

SUBMODULES: tuple[str, ...] = (
    "_section_models",
    "_section_registry",
    "_validation",
    "_step_contract",
    "_noop",
    "_plan_step",
    "_size_limits",
)

OWNED_SYMBOLS: dict[str, tuple[str, ...]] = {
    "_plan_step": ("PlanStep",),
    "_section_registry": ("PLAN_ARTIFACT_TYPE", "SectionMode", "PLAN_SECTION_NAMES"),
    "_step_contract": ("StepType", "requires_targets", "requires_verify_handle"),
    "_noop": ("PlanArtifactValidationError", "is_noop_plan"),
    "_validation": ("PlanArtifact", "normalize_plan_artifact_content"),
    "_size_limits": (
        "PLAN_SIZE_LIMITS",
        "PlanSizeLimits",
        "PlanArtifactSizeError",
        "check_plan_size",
    ),
}


@pytest.mark.parametrize("submodule_name", SUBMODULES)
def test_submodule_imports_in_isolation(submodule_name: str) -> None:
    mod = importlib.import_module(f"ralph.mcp.artifacts.plan.{submodule_name}")
    assert inspect.ismodule(mod)


def test_all_public_symbols_still_importable() -> None:
    names = set(plan_pkg.__all__)
    missing = [n for n in names if not hasattr(plan_pkg, n)]
    assert not missing, f"public symbols missing from package: {missing}"


def test_legacy_json_step_mutation_surface_is_retired() -> None:
    retired_names = {
        "insert_plan_step",
        "insert_plan_step_with_echo",
        "move_plan_step",
        "move_plan_step_with_echo",
        "remove_plan_step",
        "remove_plan_step_with_echo",
        "replace_plan_step",
        "replace_plan_step_with_echo",
    }

    assert retired_names.isdisjoint(plan_pkg.__all__)
    assert all(not hasattr(plan_pkg, name) for name in retired_names)


@pytest.mark.parametrize(
    "submodule_name,expected",
    list(OWNED_SYMBOLS.items()),
)
def test_submodule_owners(submodule_name: str, expected: tuple[str, ...]) -> None:
    mod = importlib.import_module(f"ralph.mcp.artifacts.plan.{submodule_name}")
    for name in expected:
        assert hasattr(mod, name), f"submodule {submodule_name!r} is expected to own {name!r}"


def test_size_limits_submodule_owners() -> None:
    """The new _size_limits submodule owns the 4 cap-related public symbols."""
    mod = importlib.import_module("ralph.mcp.artifacts.plan._size_limits")
    for name in (
        "PLAN_SIZE_LIMITS",
        "PlanSizeLimits",
        "PlanArtifactSizeError",
        "check_plan_size",
    ):
        assert hasattr(mod, name), f"_size_limits is expected to own {name!r}"


def test_size_limits_invariants_runtime_checks() -> None:
    """PlanSizeLimits rejects bad values at construction time (not via Pydantic)."""
    # max_total_bytes=0 must raise RuntimeError
    with pytest.raises(RuntimeError, match="must be positive"):
        PlanSizeLimits(max_total_bytes=0)

    # max_total_bytes > 64 MB must raise RuntimeError
    with pytest.raises(RuntimeError, match="must be <= 64000000"):
        PlanSizeLimits(max_total_bytes=10**9)

    # Non-positive cap must raise
    with pytest.raises(RuntimeError, match="max_steps must be positive"):
        PlanSizeLimits(max_steps=0)

    # Non-monotonic string tiers must raise
    with pytest.raises(RuntimeError, match="non-decreasing sequence"):
        PlanSizeLimits(
            max_string_short=2000,
            max_string_medium=1000,
            max_string_long=20000,
        )
