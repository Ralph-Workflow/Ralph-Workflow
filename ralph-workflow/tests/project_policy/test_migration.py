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
        "# Contributing\n\n## Testing policy\n\nRun pytest.\n",
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
        "# Contributing\n\n## Testing policy\n\n"
        + markers.MIGRATED_MARKER_TEMPLATE.format(target="testing-policy.md")
        + "\n",
    )
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path["CONTRIBUTING.md"].resolved is True


def test_candidate_with_malformed_marker_is_unresolved() -> None:
    ws = MemoryWorkspace()
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# target")
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\n## Testing policy\n\n"
        + markers.MIGRATED_MARKER_TEMPLATE.format(target="testing-policy.md")
        + " see also other policies\n",
    )
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path["CONTRIBUTING.md"].resolved is False


def test_candidate_with_suffixed_marker_is_unresolved() -> None:
    ws = MemoryWorkspace()
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# target")
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\n## Testing policy\n\n"
        + markers.MIGRATED_MARKER_TEMPLATE.format(target="testing-policy.md")
        + " extra\n",
    )
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path["CONTRIBUTING.md"].resolved is False


def test_candidate_with_arbitrary_target_marker_is_unresolved() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\n## Testing policy\n\n"
        + markers.MIGRATED_MARKER_TEMPLATE.format(target="custom-policy.md")
        + "\n",
    )
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path["CONTRIBUTING.md"].resolved is False


def test_candidate_with_partial_marker_text_is_unresolved() -> None:
    ws = MemoryWorkspace()
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# target")
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\n## Testing policy\n\n"
        + "<!-- ralph-workflow-policy:migrated -> docs/ralph-workflow-policy/ -->\n",
    )
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path["CONTRIBUTING.md"].resolved is False


def test_candidate_with_conditional_marker_is_resolved() -> None:
    ws = MemoryWorkspace()
    ws.write(f"{markers.CANONICAL_DIR}design-system-policy.md", "# target")
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\n## Testing policy\n\n"
        + markers.MIGRATED_MARKER_TEMPLATE.format(target="design-system-policy.md")
        + "\n",
    )
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path["CONTRIBUTING.md"].resolved is True


def test_candidate_without_target_is_unresolved() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "CONTRIBUTING.md",
        "# Contributing\n\n## Testing policy\n\nRun pytest.\n",
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
    assert all(c.path != "CONTRIBUTING.md" for c in candidates)


def test_doc_already_under_canonical_dir_is_skipped() -> None:
    ws = MemoryWorkspace()
    ws.write(
        f"{markers.CANONICAL_DIR}misc.md",
        "# Misc\n\n## Testing policy\n\nShould not be flagged.\n",
    )
    candidates = evidence.migration_candidates(ws)
    assert all(c.path != f"{markers.CANONICAL_DIR}misc.md" for c in candidates)


def test_agents_md_policy_sections_are_migration_candidates() -> None:
    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        "# Agents\n\n## Testing policy\n\nAlways run pytest before committing.\n",
    )
    candidates = evidence.migration_candidates(ws)
    agents_candidates = [c for c in candidates if c.path == markers.AGENTS_MD]
    assert agents_candidates, "AGENTS.md with a policy heading must be a candidate"
    assert not agents_candidates[0].resolved


def test_bootstrap_placeholder_block_does_not_make_agents_md_a_candidate() -> None:
    from ralph.project_policy import agents_md

    ws = MemoryWorkspace()
    agents_md.bootstrap(ws)
    candidates = evidence.migration_candidates(ws)
    assert all(c.path != markers.AGENTS_MD for c in candidates)
