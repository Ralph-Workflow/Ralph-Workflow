"""Shared plan artifact fixtures for tests."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

DEFAULT_SKILLS_MCP: dict[str, object] = {
    "skills": [
        "test-driven-development",
        "verification-before-completion",
    ],
    "mcps": [],
}

MINIMAL_PLAN_MARKDOWN = """\
---
type: plan
schema_version: 1
---
## Summary
Test plan.

Intent: Implement the requested change.
Coverage: feature, test

## Scope
- [SC-1] Implement the requested change
  Category: feature
- [SC-2] Verify the requested change
  Category: test

## Skills MCP
Skills: test-driven-development

## Steps

### [S-1] Implement the requested change
Make the scoped change.

Type: file_change
Files:
- modify src/example.py

### [S-2] Verify the requested change
Run the focused tests.

Type: verify
Depends on: S-1
Verify: pytest -q

## Critical Files
- [CF-1] src/example.py
  Action: modify
  Changes: implement the requested change

## Risks
- [R-1] Regression
  Severity: medium
  Mitigation: Run focused tests.

## Verification
- [V-1] pytest -q
  Expect: focused tests pass
"""


def development_result_markdown(unit_id: str) -> str:
    """Return a valid development-result document for a synthetic worker."""
    return f"""\
---
type: development_result
status: completed
---
## Summary
- [SUM-1] Worker {unit_id} completed its assigned work.

## Files Changed
- [F-1] src/{unit_id}.py

## Plan Items Proven
- [S-1] Worker {unit_id} completed the requested change.

## Analysis Items Addressed
"""


def commit_cleanup_markdown(actions: Iterable[tuple[str, str]]) -> str:
    """Return a valid commit-cleanup document for action/value pairs."""
    rendered_actions = "\n".join(
        f"- [A-{index}] {action} | {value}"
        for index, (action, value) in enumerate(actions, start=1)
    )
    return f"""\
---
type: commit_cleanup
analysis_complete: true
---
## Actions
{rendered_actions}
"""


def commit_message_markdown(subject: str) -> str:
    """Return a valid conventional-commit Markdown artifact."""
    return f"""\
---
type: commit
subject: {subject}
---
## Body Summary
- [BS-1] Complete the requested repository change.

## Body Details
- [BD-1] The scoped implementation and focused verification are complete.

## Body Footer

## Files
- [F-1] src/example.py
"""


def analysis_decision_markdown(artifact_type: str, status: str) -> str:
    """Return a valid analysis-decision document with the requested status."""
    return f"""\
---
type: {artifact_type}
status: {status}
---
## Summary
- [SUM-1] Analysis completed with status {status}.
"""
