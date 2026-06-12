"""The new ``## Agent-Driven Parallel Execution`` heading must be present
in ``planning.jinja`` and the old
``## Same-Workspace Parallel Worker Rules`` heading must be absent.

This test reads the template source directly (rather than rendering it
through the custom template engine) because the source-text checks are
exactly what the audit (``audit_parallelization_dormant``) enforces on
the bundled prompt — a drift in the rendered prompt always means a drift
in the source text.
"""

from __future__ import annotations

from pathlib import Path

_PLANNING_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "ralph"
    / "prompts"
    / "templates"
    / "planning.jinja"
)


def _read_planning_template() -> str:
    return _PLANNING_TEMPLATE.read_text(encoding="utf-8")


def test_planning_prompt_contains_new_heading_and_lacks_old() -> None:
    source = _read_planning_template()
    assert "## Agent-Driven Parallel Execution" in source, (
        "planning prompt must include the new agent-driven section"
    )
    assert "## Same-Workspace Parallel Worker Rules" not in source, (
        "planning prompt must NOT include the legacy same-workspace section"
    )


def test_planning_prompt_new_section_warns_about_fan_out() -> None:
    source = _read_planning_template()
    assert "Ralph-managed fan-out is dormant" in source
    assert "sub-agents" in source
    assert "ralph coordinate claim" in source


def test_planning_prompt_keeps_unchanged_sections() -> None:
    """Sanity: the rework must not have removed the legacy rules' underlying
    contract — the planning prompt must still mention allowed_directories
    disjointness (the contract for any parallelization shape, agent-driven
    or fan-out).
    """
    source = _read_planning_template()
    assert "allowed_directories" in source
    assert ".agent" in source
    assert ".git" in source

