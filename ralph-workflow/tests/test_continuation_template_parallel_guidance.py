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

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
_CONTINUATION_TEMPLATE = _TEMPLATES_DIR / "developer_iteration_continuation.jinja"
# The PARALLEL EXECUTION body is shared verbatim with the base developer
# template via this partial; the heading itself stays in the template.
_PARALLEL_EXECUTION_PARTIAL = _TEMPLATES_DIR / "shared" / "_parallel_execution.jinja"


def _read_continuation_template() -> str:
    return "\n".join(
        (
            _CONTINUATION_TEMPLATE.read_text(encoding="utf-8"),
            _PARALLEL_EXECUTION_PARTIAL.read_text(encoding="utf-8"),
        )
    )


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


def test_continuation_template_never_references_phantom_coordinate_command() -> None:
    """``ralph coordinate`` does not exist in the Python CLI; the template
    must not mention it even as a prohibition. Instead it tells the agent
    that no coordination command exists and dispatch is the agent's job.
    """
    source = _read_continuation_template()
    assert "ralph coordinate" not in source, (
        "continuation template must not reference the nonexistent ralph coordinate command"
    )
    assert "no coordination command" in source


def test_continuation_template_encourages_proactive_subagent_use() -> None:
    """The continuation template must allow parallel sub-agents for common
    tasks (information gathering, naturally concurrent steps) even when the
    plan declares no work units.
    """
    source = _read_continuation_template()
    assert "not limited to declared work units" in source
    assert "Information gathering" in source
    assert "Naturally concurrent steps" in source
    assert "When to stay sequential" in source


def test_continuation_template_keeps_allowed_directories_contract() -> None:
    """The continuation template must mention the plan's ``Directories:``
    field so a sub-agent dispatched for a work unit knows the per-unit
    scope contract.
    """
    source = _read_continuation_template()
    assert "Directories:" in source


def test_continuation_template_new_section_warns_about_fan_out() -> None:
    """The new ``## PARALLEL EXECUTION`` block in the continuation template
    must keep the agent-driven wording so the agent knows Ralph-managed
    fan-out is dormant and the dispatch model is sub-agents.
    """
    source = _read_continuation_template()
    assert "dispatching your own sub-agents" in source
    assert "[unit-ID]" in source


def test_continuation_template_uses_shape_aware_dispatch_and_fan_in() -> None:
    source = _read_continuation_template()
    assert "compact, linear plan" in source
    assert "Do not create delegation overhead" in source
    assert "multiple subplans" in source
    assert "dedicated sub-agent for each independent unit" in source
    assert "For 4-5 units" in source
    assert "dispatch every independent ready unit in parallel" in source
    assert "collect each unit's proof" in source
    assert "cross-unit verification" in source
    assert "final acceptance verification" in source


def test_continuation_template_requires_subagent_gate_before_submission() -> None:
    """A failed continuation must not allow artifact submission before a
    sub-agent gate clears the remaining work when that capability exists.
    """
    source = _read_continuation_template()
    assert "you MUST use at least one sub-agent as a hard gate" in source
    assert "you MUST NOT submit the artifact or declare completion" in source
