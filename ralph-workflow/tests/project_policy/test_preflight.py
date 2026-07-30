"""Tests for the deterministic preflight orchestrator."""

from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, preflight, starters
from ralph.workspace.memory import MemoryWorkspace


def _stack() -> ProjectStack:
    return ProjectStack(primary_language="Python")


def test_opt_out_returns_skipped_without_writes() -> None:
    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        f"# AGENTS.md\n\n{markers.OPT_OUT_MARKER}\n\nOpted out.\n",
    )
    result = preflight.run_policy_readiness_preflight(ws, _stack())
    assert result.is_skipped()
    # No policy files were touched.
    assert result.changed_files == []


def test_unprepared_returns_remediation_required() -> None:
    ws = MemoryWorkspace()
    result = preflight.run_policy_readiness_preflight(ws, _stack())
    assert result.requires_remediation()
    assert result.findings
    # Bootstrap seeded AGENTS.md and CLAUDE.md.
    assert markers.AGENTS_MD in result.changed_files
    assert markers.CLAUDE_MD in result.changed_files


def test_prepared_returns_ready() -> None:
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack())
    result = preflight.run_policy_readiness_preflight(ws, _stack())
    assert result.is_ready()


def test_cached_ready_avoids_revalidation() -> None:
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack())
    first = preflight.run_policy_readiness_preflight(ws, _stack())
    assert first.is_ready()
    # Second invocation must hit the cache.
    second = preflight.run_policy_readiness_preflight(ws, _stack())
    assert second.is_ready()
    # No additional changes were made.
    assert second.changed_files == [] or all(
        path in (markers.AGENTS_MD, markers.CLAUDE_MD)
        for path in second.changed_files
    )


def test_seeded_starters_are_not_complete() -> None:
    """Seeding a starter alone cannot yield READY (no completion marker, no facts)."""
    ws = MemoryWorkspace()
    starters.seed_starter_into(ws, "testing-policy.md")
    result = preflight.run_policy_readiness_preflight(ws, _stack())
    assert result.requires_remediation()
    paths = [f.path for f in result.findings]
    assert f"{markers.CANONICAL_DIR}testing-policy.md" in paths


def test_emit_callback_receives_status_line() -> None:
    ws = MemoryWorkspace()
    messages: list[str] = []
    preflight.run_policy_readiness_preflight(ws, _stack(), emit=messages.append)
    # REMEDIATION_REQUIRED emits one line so the run-loop can log the count.
    # READY/SKIPPED do NOT emit (the run-loop owns those brief lines to avoid
    # the duplicate-emission bug fixed for AC-14).
    assert any("remediation-required" in m for m in messages)
    assert not any("skipped" in m or "ready" in m for m in messages)


def test_opt_out_does_not_emit_when_skipped() -> None:
    """AC-14: SKIPPED does not emit from preflight; run-loop owns the line."""
    ws = MemoryWorkspace()
    ws.write(
        markers.AGENTS_MD,
        f"# AGENTS.md\n\n{markers.OPT_OUT_MARKER}\n\nOpted out.\n",
    )
    messages: list[str] = []
    result = preflight.run_policy_readiness_preflight(ws, _stack(), emit=messages.append)
    assert result.is_skipped()
    assert messages == []


def test_ready_does_not_emit_when_cached() -> None:
    """AC-14: READY (cached or freshly-validated) does not emit from preflight."""
    from tests.project_policy.test_validator import (
        _seed_agents_md,
        _seed_all_core_complete,
        _seed_claude_md,
    )

    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack())
    messages: list[str] = []
    result = preflight.run_policy_readiness_preflight(ws, _stack(), emit=messages.append)
    assert result.is_ready()
    assert messages == []


def test_unconditional_domain_not_required_emits_no_finding() -> None:
    """A project with no UI/CSS/UX/perf/memory signal must NOT require conditional files."""
    ws = MemoryWorkspace()
    result = preflight.run_policy_readiness_preflight(ws, _stack())
    paths = [f.path for f in result.findings]
    # No design-system / ux / performance / memory-usage file should appear.
    for filename in markers.CONDITIONAL_POLICY_FILES.values():
        assert f"{markers.CANONICAL_DIR}{filename}" not in paths
    # But core files ARE required.
    for filename in markers.CORE_POLICY_FILES:
        assert f"{markers.CANONICAL_DIR}{filename}" in paths


def test_stack_with_ui_framework_requires_design_system() -> None:
    ws = MemoryWorkspace()
    stack = ProjectStack(primary_language="TypeScript", frameworks=["React"])
    result = preflight.run_policy_readiness_preflight(ws, stack)
    paths = [f.path for f in result.findings]
    assert f"{markers.CANONICAL_DIR}design-system-policy.md" in paths
