"""Tests for the migration-candidate detector."""

from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import evidence, markers
from ralph.workspace.memory import MemoryWorkspace


def _stack() -> ProjectStack:
    return ProjectStack()


def test_unrelated_doc_is_never_a_candidate() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "docs/architecture.md",
        "# Architecture\n\nSome unrelated content about HTTP and JSON.\n",
    )
    candidates = evidence.migration_candidates(ws)
    assert all(c.path != "docs/architecture.md" for c in candidates)


def test_explicit_candidate_with_recognized_heading_is_a_candidate() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\n## Testing\n\nRun pytest.\n",
    )
    candidates = evidence.migration_candidates(ws)
    paths = [c.path for c in candidates]
    assert "CONTRIBUTING.md" in paths


def test_explicit_candidate_without_recognized_heading_is_not_a_candidate() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\nJust a code-of-conduct link.\n",
    )
    candidates = evidence.migration_candidates(ws)
    assert all(c.path != "CONTRIBUTING.md" for c in candidates)


def test_candidate_with_migrated_marker_is_resolved() -> None:
    ws = MemoryWorkspace()
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# target")
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\n## Testing\n\n"
        + markers.MIGRATED_MARKER_TEMPLATE.format(target="testing-policy.md")
        + "\n",
    )
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path["CONTRIBUTING.md"].resolved is True


def test_candidate_without_target_is_unresolved() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\n## Testing\n\nRun pytest.\n",
    )
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path["CONTRIBUTING.md"].resolved is False


def test_candidate_with_headings_removed_is_resolved() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\nAll about how to contribute.\n",
    )
    candidates = evidence.migration_candidates(ws)
    # The recognized heading was removed -> doc is no longer a candidate.
    assert all(c.path != "CONTRIBUTING.md" for c in candidates)


def test_doc_already_under_canonical_dir_is_skipped() -> None:
    ws = MemoryWorkspace()
    ws.write(
        f"{markers.CANONICAL_DIR}misc.md",
        "# Misc\n\n## Testing\n\nShould not be flagged.\n",
    )
    candidates = evidence.migration_candidates(ws)
    assert all(
        c.path != f"{markers.CANONICAL_DIR}misc.md" for c in candidates
    )
