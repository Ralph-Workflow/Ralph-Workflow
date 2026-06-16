"""Regression test for the on-the-wire planning MCP tool descriptions.

The 4 step-mutation tool descriptions (INSERT, REPLACE, REMOVE, MOVE) must
explicitly state the auto-reindex, depends_on-rewrite, and AC-remap behavior
on the wire so a cheaper model can rely on the implicit intentions from
`tools/list` alone. The SUBMIT_PLAN_SECTION description must reference the
4 MB total byte cap and `PlanSizeLimits.DEFAULT`. The FINALIZE_PLAN
description must reference the depends_on cycle detector.
"""

from __future__ import annotations

from ralph.mcp.tools.bridge._specs_artifacts import artifact_specs

_MUTATION_TOOLS = (
    "ralph_insert_plan_step",
    "ralph_replace_plan_step",
    "ralph_remove_plan_step",
    "ralph_move_plan_step",
)


def _descs() -> dict[str, str]:
    return {s.metadata.definition.name: s.metadata.definition.description for s in artifact_specs()}


def test_step_mutation_descriptions_contain_reindex_depends_on_satisfied_by_steps() -> None:
    """Every step-mutation tool description contains 'reindex', 'depends_on', and
    'satisfied_by_steps' so a cheaper agent reading `tools/list` can rely on the
    implicit-intention contract without reading source."""
    descs = _descs()
    for name in _MUTATION_TOOLS:
        desc = descs[name]
        assert "reindex" in desc, f"{name} description missing 'reindex': {desc[:200]}"
        assert "depends_on" in desc, f"{name} description missing 'depends_on': {desc[:200]}"
        assert "satisfied_by_steps" in desc, (
            f"{name} description missing 'satisfied_by_steps': {desc[:200]}"
        )


def test_submit_plan_section_description_references_size_limits() -> None:
    """SUBMIT_PLAN_SECTION description references the 4 MB total byte cap and
    `PlanSizeLimits.DEFAULT` so a cheaper model knows the byte cap and per-list
    caps are checked before Pydantic."""
    descs = _descs()
    desc = descs["ralph_submit_plan_section"]
    assert "4 MB" in desc, f"SUBMIT_PLAN_SECTION description missing '4 MB': {desc[:200]}"
    assert "PlanSizeLimits" in desc, (
        f"SUBMIT_PLAN_SECTION description missing 'PlanSizeLimits': {desc[:200]}"
    )
    assert "depends_on" in desc, (
        f"SUBMIT_PLAN_SECTION description missing 'depends_on': {desc[:200]}"
    )


def test_finalize_plan_description_references_cycle_detector() -> None:
    """FINALIZE_PLAN description references the depends_on cycle detector so a
    cheaper model knows a cyclic graph is rejected with a named entry point."""
    descs = _descs()
    desc = descs["ralph_finalize_plan"]
    assert "cycle" in desc, f"FINALIZE_PLAN description missing 'cycle': {desc[:200]}"
    assert "detected at step" in desc, (
        f"FINALIZE_PLAN description missing 'detected at step': {desc[:200]}"
    )


def test_submit_artifact_description_references_pydantic_and_byte_cap() -> None:
    """SUBMIT_ARTIFACT description references Pydantic validation and the 4 MB
    byte cap so the byte cap and the Pydantic ordering are explicit on the wire."""
    descs = _descs()
    desc = descs["ralph_submit_artifact"]
    assert "Pydantic" in desc, f"SUBMIT_ARTIFACT description missing 'Pydantic': {desc[:200]}"
    assert "4 MB" in desc, f"SUBMIT_ARTIFACT description missing '4 MB': {desc[:200]}"


def test_read_only_tool_descriptions_mark_themselves_as_noop_or_draft() -> None:
    """GET_PLAN_DRAFT and DISCARD_PLAN_DRAFT descriptions each contain 'noop' or
    'draft' so a cheaper model can identify them as read-only/noop tools
    without needing reindex keywords."""
    descs = _descs()
    for name in ("ralph_get_plan_draft", "ralph_discard_plan_draft"):
        desc = descs[name]
        assert ("noop" in desc) or ("draft" in desc), (
            f"{name} description missing 'noop' or 'draft': {desc[:200]}"
        )
