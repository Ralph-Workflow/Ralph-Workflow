from __future__ import annotations

import contextlib

from ralph.project_policy import validators
from ralph.workspace.memory import MemoryWorkspace


def test_validate_existing_policy_file_with_workspace_restores_prior_slot() -> None:
    sentinel = MemoryWorkspace()
    sentinel.write("/sentinel-marker.txt", "sentinel")
    validators.set_citation_workspace_slot(sentinel)

    inner = MemoryWorkspace()
    inner.write("/inner-marker.txt", "inner")
    content = "ralph\n"

    from ralph.project_policy import starters

    _ = starters.read_starter
    validators._validate_existing_policy_file_with_workspace(
        inner,
        content,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )

    assert validators.get_citation_workspace_slot() is sentinel, (
        "citation workspace slot was not restored to the prior value; "
        "a nested or concurrent validator will see a stale leak"
    )


def test_validate_existing_policy_file_with_workspace_restores_on_exception() -> None:
    sentinel = MemoryWorkspace()
    validators.set_citation_workspace_slot(sentinel)

    inner = MemoryWorkspace()
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
