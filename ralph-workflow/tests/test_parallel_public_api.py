"""Regression tests: ralph.pipeline.parallel public API is pinned at v1 surface.

These tests assert that the parallel package's __all__ exposes exactly the
supported v1 primitives and nothing more. Any addition to __all__ requires an
explicit decision to expand the supported surface.
"""

from __future__ import annotations


def test_parallel_all_equals_v1_surface() -> None:
    """ralph.pipeline.parallel.__all__ must list exactly the v1 public surface."""
    from ralph.pipeline import parallel  # noqa: PLC0415

    expected = {"ParallelExecutionMode", "SameWorkspaceContext", "validate_for_same_workspace"}
    actual = set(parallel.__all__)
    assert actual == expected, (
        f"ralph.pipeline.parallel.__all__ has drifted from the v1 surface.\n"
        f"  Expected: {sorted(expected)}\n"
        f"  Actual:   {sorted(actual)}\n"
        "Add new public symbols only after an explicit decision to expand the v1 surface."
    )


def test_parallel_execution_mode_has_exactly_one_member() -> None:
    """ParallelExecutionMode must have exactly one member: 'same_workspace'."""
    from ralph.pipeline.parallel import ParallelExecutionMode  # noqa: PLC0415

    members = list(ParallelExecutionMode)
    assert len(members) == 1, (
        f"ParallelExecutionMode must have exactly one member in v1, got {members!r}. "
        "Only 'same_workspace' is supported."
    )
    assert members[0].value == "same_workspace", (
        f"ParallelExecutionMode's single member must be 'same_workspace', "
        f"got {members[0].value!r}."
    )


def test_worktree_fan_out_is_not_importable() -> None:
    """WorktreeFanOut must not exist in ralph.pipeline.parallel."""
    import importlib  # noqa: PLC0415

    parallel = importlib.import_module("ralph.pipeline.parallel")
    assert not hasattr(parallel, "WorktreeFanOut"), (
        "WorktreeFanOut must not be exported from ralph.pipeline.parallel in v1"
    )


def test_parallel_package_docstring_mentions_same_workspace() -> None:
    """Package docstring must explicitly describe same-workspace mode."""
    from ralph.pipeline import parallel  # noqa: PLC0415

    doc = parallel.__doc__ or ""
    assert "same-workspace" in doc, (
        "ralph.pipeline.parallel.__doc__ must contain 'same-workspace' to "
        "make the v1 product boundary visible to users importing the package."
    )
