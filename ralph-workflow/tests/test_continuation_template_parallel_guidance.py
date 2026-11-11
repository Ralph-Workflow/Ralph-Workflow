from __future__ import annotations

from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
_CONTINUATION_TEMPLATE = _TEMPLATES_DIR / "developer_iteration_continuation.jinja"
_PARALLEL_EXECUTION_PARTIAL = _TEMPLATES_DIR / "shared" / "_parallel_execution.jinja"

_REQUIRED_ANCHORS: tuple[str, ...] = (
    "Parallel execution of independent ready units is required by default",
    "dispatch every ready unit concurrently",
    "Sequential execution requires an explicit plan reason",
    "not a reason to stop, hand back, split the task, or return `partial`",
    "which plan text forced it",
    "## PARALLEL EXECUTION (required by default)",
)

_PRESERVED_LITERALS: tuple[str, ...] = (
    "Execute only coupled steps",
    "collect each unit's",
    "cross-unit and",
    "full verification",
    "disjoint file ownership",
    "declared directory limits",
    "Do not broaden file ownership",
)

_RELIC_OPTIONAL_DISPATCH = "When the plan declares independent units, dispatch"


def _read_continuation_template() -> str:
    return "\n".join(
        (
            _CONTINUATION_TEMPLATE.read_text(encoding="utf-8"),
            _PARALLEL_EXECUTION_PARTIAL.read_text(encoding="utf-8"),
        )
    )


def test_continuation_template_contains_new_heading() -> None:
    source = _read_continuation_template()
    assert "## PARALLEL EXECUTION (required by default)" in source, (
        "continuation template must include the parallel-by-default heading"
    )


def test_continuation_template_pins_mandatory_parallel_anchors() -> None:
    source = _read_continuation_template()
    missing = [anchor for anchor in _REQUIRED_ANCHORS if anchor not in source]
    assert not missing, f"continuation template missing mandatory anchors: {missing!r}"


def test_continuation_template_keeps_preserved_literals() -> None:
    source = _read_continuation_template()
    missing = [literal for literal in _PRESERVED_LITERALS if literal not in source]
    assert not missing, f"continuation template dropped preserved literals: {missing!r}"


def test_continuation_template_keeps_fan_in_anchors() -> None:
    source = _read_continuation_template()
    assert "collect each unit's" in source
    assert "cross-unit and" in source
    assert "full verification" in source


def test_continuation_template_requires_fresh_review_before_submission() -> None:
    source = _read_continuation_template()
    assert "independent read-only sub-agent" in source
    assert "fresh-context" in source
    assert "you MUST NOT submit the artifact or declare completion" in source


def test_continuation_template_drops_optional_dispatch_relic() -> None:
    source = _read_continuation_template()
    assert _RELIC_OPTIONAL_DISPATCH not in source, (
        "continuation template must not keep the optional-dispatch opening"
    )
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
    source = _read_continuation_template()
    assert "sub-agents" in source


def test_continuation_template_keeps_allowed_directories_contract() -> None:
    source = _read_continuation_template()
    assert "Directories:" in source


def test_continuation_template_never_references_phantom_coordinate_command() -> None:
    source = _read_continuation_template()
    assert "ralph coordinate" not in source, (
        "continuation template must not reference the nonexistent ralph coordinate command"
    )


def test_continuation_template_states_continuation_keeps_parallelizing() -> None:
    continuation_source = _CONTINUATION_TEMPLATE.read_text(encoding="utf-8")
    assert "continuation" in continuation_source
    assert "paralleliz" in continuation_source
    assert "{% include 'shared/_subagents.j2' %}" in continuation_source
