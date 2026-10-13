"""Regression test for the citation-workspace-slot restore bug.

The validator publishes the citation ``path:`` workspace seam via a
module-level slot
(:data:`ralph.project_policy._content_checks._CITATION_PATH_WORKSPACE`)
for the lifetime of one call. The slot must be restored to its
PRIOR value on every exit path so a nested or concurrent validator
sees the right workspace, not a stale leak from a previous call.

The previous implementation had a duplicated assignment block that
captured ``previous`` AFTER the slot was already overwritten with
the new workspace, so the ``finally`` block restored the NEW
workspace instead of the prior one. This test pins the contract on
the inner-most helper that owns the slot.
"""

from __future__ import annotations

import contextlib

from ralph.project_policy import validators
from ralph.workspace.memory import MemoryWorkspace


def test_validate_existing_policy_file_with_workspace_restores_prior_slot() -> None:
    """The helper MUST restore the citation-workspace slot to its
    prior value after the call returns.

    Set the slot to a known sentinel, call the helper with a
    different workspace, and assert the slot is back to the
    sentinel. A regression that re-assigns ``previous`` AFTER the
    new value is set would restore the new workspace and the test
    would fail.
    """
    sentinel = MemoryWorkspace()
    sentinel.write("/sentinel-marker.txt", "sentinel")
    validators.set_citation_workspace_slot(sentinel)

    inner = MemoryWorkspace()
    inner.write("/inner-marker.txt", "inner")
    # The helper signature takes a content/path/filename plus the
    # workspace; we synthesize a minimal content that exercises
    # the seam without forcing a content-shape rejection.
    content = "ralph\n"  # placeholder; the helper only reads the slot for citations

    # Use a real bundled starter filename so the validator does not
    # reject the call on shape grounds before it gets to the
    # ``finally`` block.
    from ralph.project_policy import starters

    _ = starters.read_starter  # ensure starters are imported
    validators._validate_existing_policy_file_with_workspace(
        inner,
        content,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )

    # The slot must be back to the sentinel after the call.
    assert validators.get_citation_workspace_slot() is sentinel, (
        "citation workspace slot was not restored to the prior value; "
        "a nested or concurrent validator will see a stale leak"
    )


def test_validate_existing_policy_file_with_workspace_restores_on_exception() -> None:
    """The ``finally`` block must restore the slot even when the
    inner call raises. A regression that drops the ``try`` /
    ``finally`` (or that captures ``previous`` AFTER the new value
    is set) leaks the new workspace on every exception path.
    """
    sentinel = MemoryWorkspace()
    validators.set_citation_workspace_slot(sentinel)

    inner = MemoryWorkspace()
    # Force an exception inside the inner helper by passing a
    # filename whose path is non-string and triggers a TypeError
    # deep in the validator. The slot must still be restored.
    invalid_path: object = None
    with contextlib.suppress(Exception):
        validators._validate_existing_policy_file_with_workspace(
            inner,
            "x",
            invalid_path,
            "x.md",
        )

    assert validators.get_citation_workspace_slot() is sentinel, (
        "citation workspace slot was not restored after an exception; "
        "the finally block is missing or captures the wrong value"
    )
