"""Tests for the shared readiness-evidence inventory.

All tests use MemoryWorkspace; no real filesystem I/O.
"""

from __future__ import annotations

import json

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import evidence, markers
from ralph.workspace.memory import MemoryWorkspace


def _stack(*, primary="Python", secondary=(), frameworks=()):
    return ProjectStack(
        primary_language=primary,
        secondary_languages=list(secondary),
        frameworks=list(frameworks),
    )


def test_required_languages_includes_primary_excludes_non_code() -> None:
    stack = _stack(primary="Python", secondary=["JSON", "TypeScript"])
    langs = evidence.required_languages(stack)
    assert "Python" in langs
    assert "TypeScript" in langs
    assert "JSON" not in langs


def test_required_languages_empty_for_unknown_primary() -> None:
    stack = _stack(primary="Unknown")
    assert evidence.required_languages(stack) == set()


def test_design_system_required_for_ui_framework() -> None:
    ws = MemoryWorkspace()
    stack = _stack(frameworks=["React"])
    required, triggered = evidence.design_system_required(ws, stack)
    assert required is True
    assert "React" in triggered


def test_design_system_required_for_css_language() -> None:
    ws = MemoryWorkspace()
    stack = _stack(secondary=["CSS"])
    required, triggered = evidence.design_system_required(ws, stack)
    assert required is True
    assert "CSS" in triggered


def test_design_system_required_without_ux() -> None:
    """A project with CSS but no router-dep / app framework triggers design-system only."""
    ws = MemoryWorkspace()
    stack = _stack(secondary=["CSS"])
    assert evidence.design_system_required(ws, stack)[0] is True
    assert evidence.ux_required(ws, stack)[0] is False


def test_ux_required_for_app_framework() -> None:
    ws = MemoryWorkspace()
    stack = _stack(frameworks=["Angular"])
    assert evidence.ux_required(ws, stack)[0] is True


def test_ux_required_for_router_dependency() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "package.json",
        json.dumps({"dependencies": {"react-router-dom": "^6.0.0"}}),
    )
    stack = _stack(primary="JavaScript", secondary=["TypeScript"])
    assert evidence.ux_required(ws, stack)[0] is True


def test_performance_required_for_signal_file() -> None:
    ws = MemoryWorkspace()
    ws.write("performance-budget.json", '{"budget": 200}')
    stack = _stack()
    assert evidence.performance_required(ws, stack)[0] is True


def test_performance_required_for_dep_substring() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "package.json",
        json.dumps({"devDependencies": {"k6": "^1.0.0"}}),
    )
    stack = _stack()
    assert evidence.performance_required(ws, stack)[0] is True


def test_performance_not_required_without_signals() -> None:
    ws = MemoryWorkspace()
    stack = _stack()
    assert evidence.performance_required(ws, stack)[0] is False


def test_memory_required_for_signal_file() -> None:
    ws = MemoryWorkspace()
    ws.write("docs/memory-budget.md", "# Memory budget\n\n128 MB heap")
    stack = _stack()
    assert evidence.memory_required(ws, stack)[0] is True


def test_memory_required_for_dep_substring() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "package.json",
        json.dumps({"devDependencies": {"memlab": "^1.0.0"}}),
    )
    stack = _stack()
    assert evidence.memory_required(ws, stack)[0] is True


def test_memory_not_required_without_signals() -> None:
    ws = MemoryWorkspace()
    stack = _stack()
    assert evidence.memory_required(ws, stack)[0] is False


def test_readiness_evidence_includes_all_twelve_policy_paths() -> None:
    ws = MemoryWorkspace()
    stack = _stack()
    entries = evidence.readiness_evidence(ws, stack)
    paths = {entry.rel_path for entry in entries}
    for filename in markers.CORE_POLICY_FILES:
        assert f"{markers.CANONICAL_DIR}{filename}" in paths
    for filename in markers.CONDITIONAL_POLICY_FILES.values():
        assert f"{markers.CANONICAL_DIR}{filename}" in paths
    assert markers.AGENTS_MD in paths
    assert markers.CLAUDE_MD in paths


def test_readiness_evidence_records_deletion_signature() -> None:
    ws = MemoryWorkspace()
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# testing")
    stack = _stack()
    before = evidence.evidence_signature(ws, stack)
    ws.remove(f"{markers.CANONICAL_DIR}testing-policy.md")
    after = evidence.evidence_signature(ws, stack)
    assert before != after


def test_readiness_evidence_records_edit_signature() -> None:
    ws = MemoryWorkspace()
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# testing")
    stack = _stack()
    before = evidence.evidence_signature(ws, stack)
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# testing updated")
    after = evidence.evidence_signature(ws, stack)
    assert before != after


def test_migration_candidates_skips_unrecognized_docs() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "docs/api-design.md",
        "# API design\n\nSome unrelated content about HTTP status codes.\n",
    )
    _stack()
    candidates = evidence.migration_candidates(ws)
    assert all(c.path != "docs/api-design.md" for c in candidates)


def test_migration_candidates_flags_recognized_heading() -> None:
    ws = MemoryWorkspace()
    ws.write(
        "docs/testing.md",
        "# Testing Policy\n\nOur testing policy is documented here.\n",
    )
    _stack()
    candidates = evidence.migration_candidates(ws)
    paths = [c.path for c in candidates]
    assert "docs/testing.md" in paths


def test_migration_candidates_resolved_with_migrated_marker() -> None:
    ws = MemoryWorkspace()
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", "# target\n")
    ws.write(
        "docs/testing.md",
        "# Testing Policy\n\n"
        + markers.MIGRATED_MARKER_TEMPLATE.format(target="testing-policy.md")
        + "\n",
    )
    _stack()
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path.get("docs/testing.md") is not None
    assert by_path["docs/testing.md"].resolved is True
