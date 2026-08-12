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
    source = _read_continuation_template()
    assert "ralph coordinate" not in source, (
        "continuation template must not reference the nonexistent ralph coordinate command"
    )


def test_continuation_template_limits_subagents_to_independent_units() -> None:
    source = _read_continuation_template()
    assert "plan declares independent units" in source
    assert "disjoint file ownership" in source


def test_continuation_template_keeps_allowed_directories_contract() -> None:
    """The continuation template must mention the plan's ``Directories:``
    field so a sub-agent dispatched for a work unit knows the per-unit
    scope contract.
    """
    source = _read_continuation_template()
    assert "declared directory limits" in source


def test_continuation_template_new_section_warns_about_fan_out() -> None:
    """The new ``## PARALLEL EXECUTION`` block in the continuation template
    must keep the agent-driven wording so the agent knows Ralph-managed
    fan-out is dormant and the dispatch model is sub-agents.
    """
    source = _read_continuation_template()
    assert "dispatch ready units concurrently" in source


def test_continuation_template_uses_shape_aware_dispatch_and_fan_in() -> None:
    source = _read_continuation_template()
    assert "compact or coupled steps" in source
    assert "execute\nunits sequentially in plan order" in source
    assert "collect each unit's" in source
    assert "cross-unit and" in source
    assert "full verification" in source


def test_continuation_template_requires_fresh_review_before_submission() -> None:
    """A continuation requires review without imposing coordination overhead."""
    source = _read_continuation_template()
    assert "when coordination costs less than sequential execution" in source
    assert "otherwise perform the same review sequentially" in source
    assert "you MUST NOT submit the artifact or declare completion" in source
