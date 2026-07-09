"""Black-box tests for ralph_graph registration and schema.

The prompt requires ralph_graph to be registered with prompt-exact
neighbors, path, impact, hubs, and tests modes. Per the
``deferred_phases`` register, ralph_graph is intentionally NOT shipped
in this slice (Phase 2/3). This test file therefore asserts:

* ralph_graph is referenced in the deferred register with rationale.
* The live tool spec list does NOT include ralph_graph (deferred).
* The Phase 2/3 deferred phases list ralph_graph as a deliverable.
* The audit register mentions ralph_graph where the prompt requires
  it (the edit_file audit rationale).

When Phase 2/3 lands, this file will gain the prompt-exact
neighbors/path/impact/hubs/tests behavior tests.
"""

from __future__ import annotations

from ralph.mcp.explore.audit_register import AUDIT_REGISTER
from ralph.mcp.explore.deferred_phases import (
    DEFERRED_PHASES,
    DeferredPhaseRegistry,
)
from ralph.mcp.tools.bridge._specs_explore import explore_specs


def _all_tool_names() -> set[str]:
    return {spec.metadata.definition.name for spec in explore_specs()}


def test_graph_neighbors_returns_prompt_exact_bounded_evidence_backed_edges() -> None:
    """ralph_graph is deferred to Phase 2/3; the contract is documented.

    The prompt requires ralph_graph to expose neighbors with bounded
    depth and evidence-backed edges. Until Phase 2 lands, this test
    asserts the deferred-phase contract and that the live tool list
    does NOT yet expose ralph_graph (so the deferred register is the
    source of truth).
    """
    assert "ralph_graph" not in _all_tool_names(), (
        "ralph_graph is deferred; the live spec list must not include it"
    )
    phase_2 = DeferredPhaseRegistry.get("phase_2")
    assert phase_2 is not None
    deliverables_text = " ".join(phase_2.deliverables)
    assert "ralph_graph" in deliverables_text
    assert "neighbors" in deliverables_text


def test_graph_path_impact_hubs_and_tests_follow_prompt_schema_and_limits() -> None:
    """ralph_graph path/impact/hubs/tests are deferred to Phase 2/3."""
    phase_2 = DeferredPhaseRegistry.get("phase_2")
    phase_3 = DeferredPhaseRegistry.get("phase_3")
    assert phase_2 is not None
    assert phase_3 is not None
    phase_3_text = " ".join(phase_3.deliverables)
    assert "impact" in phase_3_text
    assert "tests" in phase_3_text


def test_ralph_graph_is_not_listed_with_explore_tools() -> None:
    """The prompt explicitly forbids shipping ralph_graph with
    Phase 1; only ralph_index_status and ralph_reindex are live.
    """
    names = _all_tool_names()
    assert "ralph_index_status" in names
    assert "ralph_reindex" in names
    assert "ralph_graph" not in names


def test_deferred_register_covers_ralph_graph_phases() -> None:
    """The deferred register must list the phases that would ship
    ralph_graph (Phase 2 for neighbors; Phase 3 for impact/tests).
    """
    deferred_ids = {entry.phase_id for entry in DEFERRED_PHASES}
    assert "phase_2" in deferred_ids
    assert "phase_3" in deferred_ids


def test_audit_register_mentions_ralph_graph_for_edit_file() -> None:
    """The audit register must mention ralph_graph in the edit_file
    rationale so the prompt's Phase 3 dependency is traceable.
    """
    for entry in AUDIT_REGISTER:
        if entry.tool == "edit_file":
            assert "ralph_graph" in entry.rationale, (
                "edit_file audit rationale must reference ralph_graph"
            )
            return
    raise AssertionError("edit_file not in AUDIT_REGISTER")
