"""The continuation template must carry the same agent-driven parallel
execution guidance as the regular ``developer_iteration.jinja`` template.

This test reads ``developer_iteration_continuation.jinja`` source
directly (rather than rendering it through the custom template
engine) because the source-text checks are exactly what the audit
(``audit_parallelization_dormant`` invariant #7) enforces on the
bundled prompt — a drift in the rendered prompt always means a drift
in the source text.
"""

from __future__ import annotations

from pathlib import Path

_CONTINUATION_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "ralph"
    / "prompts"
    / "templates"
    / "developer_iteration_continuation.jinja"
)


def _read_continuation_template() -> str:
    return _CONTINUATION_TEMPLATE.read_text(encoding="utf-8")


def test_continuation_template_contains_new_heading() -> None:
    """The new ``## PARALLEL EXECUTION`` heading must be present in the
    continuation template so a non-initial-iteration run still tells the
    executing agent to dispatch sub-agents.
    """
    source = _read_continuation_template()
    assert "## PARALLEL EXECUTION" in source, (
        "continuation template must include the new agent-driven section"
    )


def test_continuation_template_mentions_sub_agents() -> None:
    """The continuation template must reference sub-agents so the agent
    knows the parallel-execution contract delegates to its own tooling.
    """
    source = _read_continuation_template()
    assert "sub-agents" in source


def test_continuation_template_forbids_ralph_coordinate_claim() -> None:
    """The continuation template must keep the ``ralph coordinate claim``
    guard (in its forbidden form) so the agent is told not to drive
    parallel work through the coordination tool. This is the same
    substring the new audit invariant locks in via the source-text
    contract.
    """
    source = _read_continuation_template()
    assert "ralph coordinate claim" in source, (
        "continuation template must explicitly forbid ralph coordinate claim "
        "as a parallel-execution mechanism"
    )


def test_continuation_template_keeps_allowed_directories_contract() -> None:
    """The continuation template must mention ``allowed_directories`` so
    a sub-agent dispatched for a work unit knows the per-unit scope
    contract.
    """
    source = _read_continuation_template()
    assert "allowed_directories" in source


def test_continuation_template_new_section_warns_about_fan_out() -> None:
    """The new ``## PARALLEL EXECUTION`` block in the continuation template
    must keep the agent-driven wording so the agent knows Ralph-managed
    fan-out is dormant and the dispatch model is sub-agents.
    """
    source = _read_continuation_template()
    assert "dispatching your own sub-agents" in source
    assert "unit_id" in source
