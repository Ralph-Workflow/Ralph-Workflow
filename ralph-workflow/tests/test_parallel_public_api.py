from __future__ import annotations

import importlib

from ralph.pipeline import parallel
from ralph.pipeline.parallel import ParallelExecutionMode

RALPH_TEST_CHANGE_REASONS = {
    "test_parallel_package_docstring_mentions_same_workspace": (
        "the docstring mandate is retired by the absolute no-docstring policy; "
        "the check now asserts the package carries no docstring, which is the "
        "same contract in the opposite direction"
    ),
}


def test_parallel_all_equals_v1_surface() -> None:

    expected = {"ParallelExecutionMode", "SameWorkspaceContext"}
    actual = set(parallel.__all__)
    assert actual == expected, (
        f"ralph.pipeline.parallel.__all__ has drifted from the v1 surface.\n"
        f"  Expected: {sorted(expected)}\n"
        f"  Actual:   {sorted(actual)}\n"
        "Add new public symbols only after an explicit decision to expand the v1 surface."
    )


def test_parallel_execution_mode_has_exactly_one_member() -> None:

    members = list(ParallelExecutionMode)
    assert len(members) == 1, (
        f"ParallelExecutionMode must have exactly one member in v1, got {members!r}. "
        "Only 'same_workspace' is supported."
    )
    assert members[0].value == "same_workspace", (
        f"ParallelExecutionMode's single member must be 'same_workspace', got {members[0].value!r}."
    )


def test_worktree_fan_out_is_not_importable() -> None:

    parallel = importlib.import_module("ralph.pipeline.parallel")
    assert not hasattr(parallel, "WorktreeFanOut"), (
        "WorktreeFanOut must not be exported from ralph.pipeline.parallel in v1"
    )


def test_parallel_package_carries_no_docstring() -> None:
    assert parallel.__doc__ is None, (
        "ralph.pipeline.parallel must carry no docstring; contracts live in "
        "names, type annotations, and tests."
    )
