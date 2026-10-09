"""The continuation template must carry the same agent-driven parallel
execution guidance as the regular ``developer_iteration.jinja`` template.

This test reads ``developer_iteration_continuation.jinja`` source
directly (rather than rendering it through the custom template
engine) because the source-text checks are exactly what the audit
(``audit_parallelization_dormant`` invariant #7) enforces on the
bundled prompt — a drift in the rendered prompt always means a drift
in the source text.

The pinned anchors below are the shared wording contract that
parallelizes the developer prompt: parallel dispatch is required by
default, sequential execution needs an explicit plan reason, and an
ambitious plan means more parallelism, never an early stop.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
_CONTINUATION_TEMPLATE = _TEMPLATES_DIR / "developer_iteration_continuation.jinja"
# The PARALLEL EXECUTION body is shared verbatim with the base developer
# template via this partial; the heading itself stays in the template.
_PARALLEL_EXECUTION_PARTIAL = _TEMPLATES_DIR / "shared" / "_parallel_execution.jinja"

# Pinned mandatory anchors (exact literals, no normalization). Every
# coordinator developer template + the parallel-execution partial must
# carry these; the worker templates and the IS_WORKER branches must
# carry none of the coordinator-only ones.
_REQUIRED_ANCHORS: tuple[str, ...] = (
    "Parallel execution of independent ready units is required by default",
    "dispatch every ready unit concurrently",
    "Sequential execution requires an explicit plan reason",
    "not a reason to stop, hand back, split the task, or return `partial`",
    "which plan text forced it",
    "## PARALLEL EXECUTION (required by default)",
)

# Preserved literals (already enforced elsewhere; kept here to make
# sure the continuation template never silently drops them while
# rewriting the opening to make parallelism the default).
_PRESERVED_LITERALS: tuple[str, ...] = (
    "dispatch ready units concurrently",
    "Execute only coupled steps",
    "collect each unit's",
    "cross-unit and",
    "full verification",
    "disjoint file ownership",
    "declared directory limits",
    "Do not broaden file ownership",
)

# Relic guard: the old optional-dispatch opening must not survive.
_RELIC_OPTIONAL_DISPATCH = "When the plan declares independent units, dispatch"


def _read_continuation_template() -> str:
    return "\n".join(
        (
            _CONTINUATION_TEMPLATE.read_text(encoding="utf-8"),
            _PARALLEL_EXECUTION_PARTIAL.read_text(encoding="utf-8"),
        )
    )


def test_continuation_template_contains_new_heading() -> None:
    """The new ``## PARALLEL EXECUTION (required by default)`` heading
    must be present in the continuation template so a non-initial-iteration
    run still tells the executing agent to dispatch sub-agents in one
    wave before starting its own implementation.
    """
    source = _read_continuation_template()
    assert "## PARALLEL EXECUTION (required by default)" in source, (
        "continuation template must include the parallel-by-default heading"
    )


def test_continuation_template_pins_mandatory_parallel_anchors() -> None:
    """The continuation template + shared partial must carry every pinned
    mandatory anchor from the shared wording contract. The contract is
    the single source of truth for "parallel is required by default".
    """
    source = _read_continuation_template()
    missing = [anchor for anchor in _REQUIRED_ANCHORS if anchor not in source]
    assert not missing, f"continuation template missing mandatory anchors: {missing!r}"


def test_continuation_template_keeps_preserved_literals() -> None:
    """The rewrite must not drop the preserved-literal set already
    enforced by the focused suite. These are the contract anchors the
    continuation template has historically carried.
    """
    source = _read_continuation_template()
    missing = [literal for literal in _PRESERVED_LITERALS if literal not in source]
    assert not missing, f"continuation template dropped preserved literals: {missing!r}"


def test_continuation_template_keeps_fan_in_anchors() -> None:
    """Fan-in language: workers return, main session integrates."""
    source = _read_continuation_template()
    assert "collect each unit's" in source
    assert "cross-unit and" in source
    assert "full verification" in source


def test_continuation_template_requires_fresh_review_before_submission() -> None:
    """A continuation requires review without imposing coordination overhead."""
    source = _read_continuation_template()
    assert "independent read-only sub-agent" in source
    assert "fresh-context" in source
    assert "you MUST NOT submit the artifact or declare completion" in source


def test_continuation_template_drops_optional_dispatch_relic() -> None:
    """The rewrite must remove the old optional-dispatch opening so the
    continuation template never reverts to "when the plan declares…".
    The narrowed guard targets the exact optional-opening literal; the
    queue-in-waves wording ("queue the remaining ready units") is the
    surviving cap-exhaustion replacement and is asserted separately.
    """
    source = _read_continuation_template()
    assert _RELIC_OPTIONAL_DISPATCH not in source, (
        "continuation template must not keep the optional-dispatch opening"
    )
    # The S-1 edit 4 split must survive in the partial: a full cap
    # queues remaining ready units; the ownership / malformed-graph
    # safeguards stay. None of the removed fallback phrases remain.
    assert "queue the remaining ready units" in source
    assert "ownership unresolvable" in source
    assert "malformed" in source
    for forbidden in (
        "or a missing sub-agent tool",
        "bounded-sequential",
        "no native sub-agent / task tool is exposed",
        "runtime limit",
    ):
        assert forbidden not in source, f"continuation template/partial re-introduced {forbidden!r}"


def test_continuation_template_mentions_sub_agents() -> None:
    """The continuation template must reference sub-agents so the agent
    knows the parallel-execution contract delegates to its own tooling.
    """
    source = _read_continuation_template()
    assert "sub-agents" in source


def test_continuation_template_keeps_allowed_directories_contract() -> None:
    """The continuation template must mention the plan's ``Directories:``
    field so a sub-agent dispatched for a work unit knows the per-unit
    scope contract.
    """
    source = _read_continuation_template()
    assert "Directories:" in source


def test_continuation_template_never_references_phantom_coordinate_command() -> None:
    source = _read_continuation_template()
    assert "ralph coordinate" not in source, (
        "continuation template must not reference the nonexistent ralph coordinate command"
    )


def test_continuation_template_states_continuation_keeps_parallelizing() -> None:
    """A continuation must not fall back to sequential just because it
    is a continuation. The continuation-only preamble must explicitly
    state that the developer keeps parallelizing remaining ready work.
    """
    continuation_source = _CONTINUATION_TEMPLATE.read_text(encoding="utf-8")
    assert "continuation" in continuation_source
    assert "paralleliz" in continuation_source
    # The continuation preamble must be inside the non-worker branch.
    assert "{% if not IS_WORKER %}" in continuation_source
