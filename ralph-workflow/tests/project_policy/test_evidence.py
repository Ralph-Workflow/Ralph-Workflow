"""Tests for the shared readiness-evidence inventory.

All tests use MemoryWorkspace; no real filesystem I/O.
"""

from __future__ import annotations

import json

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import evidence, markers
from ralph.workspace.memory import MemoryWorkspace


def _stack(
    *,
    primary: str = "Python",
    secondary: list[str] | tuple[str, ...] = (),
    frameworks: list[str] | tuple[str, ...] = (),
) -> ProjectStack:
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


def test_router_only_project_requires_both_design_system_and_ux() -> None:
    """AC-07: a project whose only UX signal is a router dependency must
    require BOTH design-system AND ux (UX implies design-system).
    """
    ws = MemoryWorkspace()
    package_json: dict[str, dict[str, str]] = {
        "dependencies": {"react-router-dom": "^6.0.0"}
    }
    ws.write("package.json", json.dumps(package_json))
    stack = _stack(primary="JavaScript", secondary=["TypeScript"], frameworks=[])
    ds_required, ds_triggered = evidence.design_system_required(ws, stack)
    ux_required, _ = evidence.ux_required(ws, stack)
    assert ux_required is True
    assert ds_required is True, (
        f"design-system must be required when UX is required; triggered={ds_triggered}"
    )
    assert any("ux_implies_design_system" in t for t in ds_triggered)


def test_design_system_required_does_not_imply_ux() -> None:
    """AC-07 symmetry: design-system can stand on its own without UX.
    A project with CSS but no UX signals triggers ONLY design-system.
    """
    ws = MemoryWorkspace()
    stack = _stack(secondary=["CSS"])
    ds_required, _ = evidence.design_system_required(ws, stack)
    ux_required, _ = evidence.ux_required(ws, stack)
    assert ds_required is True
    assert ux_required is False


def test_ux_required_for_app_framework() -> None:
    ws = MemoryWorkspace()
    stack = _stack(frameworks=["Angular"])
    assert evidence.ux_required(ws, stack)[0] is True


def test_ux_required_for_router_dependency() -> None:
    ws = MemoryWorkspace()
    package_json: dict[str, dict[str, str]] = {
        "dependencies": {"react-router-dom": "^6.0.0"}
    }
    ws.write("package.json", json.dumps(package_json))
    stack = _stack(primary="JavaScript", secondary=["TypeScript"])
    assert evidence.ux_required(ws, stack)[0] is True


def test_performance_required_for_signal_file() -> None:
    ws = MemoryWorkspace()
    ws.write("performance-budget.json", '{"budget": 200}')
    stack = _stack()
    assert evidence.performance_required(ws, stack)[0] is True


def test_performance_required_for_dep_substring() -> None:
    ws = MemoryWorkspace()
    package_json: dict[str, dict[str, str]] = {
        "devDependencies": {"k6": "^1.0.0"}
    }
    ws.write("package.json", json.dumps(package_json))
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
    package_json: dict[str, dict[str, str]] = {
        "devDependencies": {"memlab": "^1.0.0"}
    }
    ws.write("package.json", json.dumps(package_json))
    stack = _stack()
    assert evidence.memory_required(ws, stack)[0] is True


def test_memory_not_required_without_signals() -> None:
    ws = MemoryWorkspace()
    stack = _stack()
    assert evidence.memory_required(ws, stack)[0] is False


@pytest.mark.parametrize(
    ("domain", "signal_path"),
    (
        ("api-compatibility", "openapi.yaml"),
        ("data-storage", "db/migrate/"),
        ("reliability-observability", "docs/operations.md"),
        ("privacy", "docs/data-classification.md"),
        ("release-deployment", ".github/workflows/release.yml"),
    ),
)
def test_conditional_domain_requires_exact_repository_signal(
    domain: str, signal_path: str
) -> None:
    ws = MemoryWorkspace()
    if signal_path.endswith("/"):
        ws.mkdirs(signal_path.rstrip("/"))
    else:
        ws.write(signal_path, "verified signal")
    requirements = evidence.conditional_domain_requirements(ws, _stack())
    required, triggers = requirements[domain]
    assert required is True
    assert signal_path in triggers


def test_specialized_conditional_domains_remain_optional_without_exact_signals() -> None:
    requirements = evidence.conditional_domain_requirements(
        MemoryWorkspace(), _stack()
    )
    for domain in markers.CONDITIONAL_SIGNAL_PATHS:
        assert requirements[domain] == (False, [])


def test_readiness_evidence_includes_all_policy_paths() -> None:
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


def test_migration_candidates_flags_root_security_md() -> None:
    """A project's existing SECURITY.md (standard GitHub convention) must be
    pulled into the canonical security policy during remediation — otherwise
    a parallel security rulebook survives outside the canonical directory."""
    ws = MemoryWorkspace()
    ws.write(
        "SECURITY.md",
        "# Security Policy\n\nReport vulnerabilities to security@example.com.\n",
    )
    candidates = evidence.migration_candidates(ws)
    by_path = {c.path: c for c in candidates}
    assert by_path.get("SECURITY.md") is not None
    assert by_path["SECURITY.md"].resolved is False


def test_migration_candidates_flags_docs_security_heading() -> None:
    """A docs/security.md with a security heading is policy-like content and
    must be flagged for migration into security-policy.md."""
    ws = MemoryWorkspace()
    ws.write(
        "docs/security.md",
        "## Security policy\n\nNever log tokens or credentials.\n",
    )
    candidates = evidence.migration_candidates(ws)
    paths = [c.path for c in candidates]
    assert "docs/security.md" in paths


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


def test_performance_required_for_directory_signal() -> None:
    """AC-07 + plan step 3: directory signal paths must trigger performance."""
    ws = MemoryWorkspace()
    ws.create_dir("benches")
    assert evidence.performance_required(ws, _stack())[0] is True


def test_memory_required_for_directory_signal() -> None:
    """AC-07 + plan step 3: directory signal paths must trigger memory-usage."""
    ws = MemoryWorkspace()
    ws.create_dir("soak-tests")
    assert evidence.memory_required(ws, _stack())[0] is True


def test_readiness_evidence_handles_directory_signal_paths() -> None:
    """AC-04 + AC-11: a project containing the plan-required ``benches/`` or
    ``soak-tests/`` directory must not crash readiness_evidence /
    evidence_signature. The directory is captured as an existing
    :class:`EvidenceEntry` with a stable sentinel hash, not read.
    """
    ws = MemoryWorkspace()
    ws.create_dir("benches")
    ws.create_dir("soak-tests")
    stack = _stack()
    # The two functions must not raise IsADirectoryError.
    entries = evidence.readiness_evidence(ws, stack)
    signature = evidence.evidence_signature(ws, stack)
    by_path = {entry.rel_path: entry for entry in entries}
    assert by_path["benches/"].exists is True
    assert by_path["benches/"].content_sha256 is not None
    assert by_path["soak-tests/"].exists is True
    assert by_path["soak-tests/"].content_sha256 is not None
    assert isinstance(signature, str) and len(signature) == 64


def test_readiness_evidence_directory_signal_signature_changes_with_contents() -> None:
    """AC-04: the readiness-evidence signature must change when files are
    added/removed inside a directory signal path (e.g. ``benches/``),
    so a cached READY cannot stay valid across such a change.
    """
    ws = MemoryWorkspace()
    ws.create_dir("benches")
    before = evidence.evidence_signature(ws, _stack())
    ws.write("benches/bench_a.txt", "alpha")
    after = evidence.evidence_signature(ws, _stack())
    assert before != after
    ws.write("benches/bench_b.txt", "beta")
    after_more = evidence.evidence_signature(ws, _stack())
    assert after != after_more


def test_readiness_evidence_directory_signal_signature_changes_on_delete() -> None:
    """AC-04: deleting a directory signal invalidates a cached READY."""
    ws = MemoryWorkspace()
    ws.create_dir("benches")
    before = evidence.evidence_signature(ws, _stack())
    ws.delete("benches", recursive=True)
    after = evidence.evidence_signature(ws, _stack())
    assert before != after
