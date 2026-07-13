"""Shared builders for a complete (validator-passing) policy corpus.

The policy pipeline's tests need to control exactly one thing: does
``validators.validate_readiness`` return findings or not? These helpers let a
fake agent "fix" a workspace by seeding a corpus that really does pass the real
deterministic validator -- so the tests drive the true gate rather than a mock of
it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, starters

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace

_LANG_POLICIES = frozenset({"typechecking-policy.md", "linting-policy.md"})


def stack() -> ProjectStack:
    """The project stack every policy-pipeline test runs against."""
    return ProjectStack(
        primary_language="Python", secondary_languages=[], frameworks=[]
    )


def complete_policy_body(filename: str) -> str:
    """Build a policy body that satisfies every structural check.

    The required RALPH-FACT keys are read from the bundled starter, so a starter
    that gains a fact key cannot silently drift away from these fixtures.
    """
    facts = [
        line.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
        for line in starters.read_starter(filename).splitlines()
        if line.startswith(markers.FACT_MARKER)
    ]
    headings = markers.REQUIRED_HEADINGS[filename]
    lines = [
        markers.POLICY_SCHEMA_MARKER,
        f"{markers.POLICY_ID_PREFIX} {filename} -->",
        "",
        "# Title",
        "",
    ]
    for heading in headings:
        lines.append(f"## {heading}")
        if heading == "Research basis":
            # The citation block must follow the heading directly: the validator
            # parses it as a block, and prose in between breaks the parse.
            lines.extend(
                (
                    "",
                    "- publisher: Test Publisher",
                    "  title: Test Title",
                    "  http: https://example.com",
                    "  review date: 2026-07-13",
                    "",
                )
            )
        elif heading == "Bypass detection":
            # The bypass gate is scoped to its section: the command must appear
            # UNDER this heading, not merely somewhere in the file.
            lines.extend(("Real content.", "RALPH-COMMAND: make bypass-audit", ""))
        else:
            lines.extend(("Real content.", ""))
    lines.extend(facts)
    lines.append(f"RALPH-FACT: {filename}: path = {markers.CANONICAL_DIR}{filename}")
    if filename in _LANG_POLICIES:
        lines.append("RALPH-LANG: Python")
    lines.append("RALPH-COMMAND: make test")
    return "\n".join(lines)


def seed_complete_corpus(workspace: Workspace) -> None:
    """Write a full policy corpus that passes the real validator."""
    workspace.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    for filename in markers.CORE_POLICY_FILES:
        workspace.write(
            f"{markers.CANONICAL_DIR}{filename}", complete_policy_body(filename)
        )
    workspace.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"See {markers.CANONICAL_DIR}.\n"
        f"{markers.AGENTS_BLOCK_END}\n",
    )
    workspace.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )


__all__ = ["complete_policy_body", "seed_complete_corpus", "stack"]
