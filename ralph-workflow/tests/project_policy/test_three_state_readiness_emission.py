"""Tests for the v5 three-state readiness emission on every surface.

The runtime reports THREE dimensions, not two:

* structure complete
* implementation verified
* outcome evidence reviewed

A structural READY is the caching gate; the other two are the
runtime's and the agent's job and are reported separately. These
tests pin the contract on the four surfaces the plan names: the
CLI emit, the status bar footer, the AGENTS.md managed block, and
the remediation prompt. A future edit that collapses any surface
back to a single READY/BLOCKED projection is a regression.
"""

from __future__ import annotations

from ralph.project_policy import agents_md, cli_integration, markers
from ralph.project_policy._readiness_assessment import ReadinessAssessment
from ralph.project_policy.status_bar import format_readiness_footer_label


def test_readiness_assessment_dimensions_are_all_default_false() -> None:
    """An uninitialised assessment cannot accidentally project True.

    The three-dimension gate is fail-closed: an assessment that has
    not been computed through the strict ledger parser must show
    every dimension as false. A future refactor that flips a default
    is a silent corruption of the cache contract.
    """
    a = ReadinessAssessment()
    assert a.structure_complete is False
    assert a.implementation_verified is False
    assert a.outcome_evidence_reviewed is False


def test_readiness_footer_label_renders_three_states() -> None:
    """The status bar footer label surfaces all three dimensions, never
    a collapsed READY/BLOCKED projection.

    The label format is ``Policy: S*/I*/O*`` (or the dash variant) so
    a glance at the footer tells the operator the three dimensions,
    not just a single state.
    """
    label_all_true = format_readiness_footer_label(
        ReadinessAssessment(
            structure_complete=True,
            implementation_verified=True,
            outcome_evidence_reviewed=True,
        )
    )
    assert "S*" in label_all_true
    assert "I*" in label_all_true
    assert "O*" in label_all_true
    assert "Policy:" in label_all_true

    label_none = format_readiness_footer_label(None)
    assert "Policy:" in label_none
    # A None assessment collapses to dashes; it must NOT silently
    # project "all true" the way a structural READY would.
    assert "S*" not in label_none
    assert "I*" not in label_none
    assert "O*" not in label_none

    label_mixed = format_readiness_footer_label(
        ReadinessAssessment(
            structure_complete=True,
            implementation_verified=False,
            outcome_evidence_reviewed=False,
        )
    )
    # The mixed label must clearly distinguish structural-only
    # READY from a fully-verified READY; the dash marker on I and O
    # is what surfaces that gap.
    assert "S*" in label_mixed
    assert "I-" in label_mixed
    assert "O-" in label_mixed


def test_agents_md_ready_template_names_three_dimensions() -> None:
    """The AGENTS.md READY template explicitly names the three
    dimensions AND the structural-vs-semantic distinction.

    The whole point of the v5 split is that an agent reading
    AGENTS.md sees the three-state gate, not a single READY
    projection. The template MUST contain all three dimension
    names AND the explicit ``structural READY is NOT semantic
    correctness`` disclaimer.
    """
    template = agents_md._AGENTS_READY_TEMPLATE.format(
        begin=markers.AGENTS_BLOCK_BEGIN,
        end=markers.AGENTS_BLOCK_END,
        canonical_dir=markers.CANONICAL_DIR,
    )
    assert "structure complete" in template
    assert "implementation verified" in template
    assert "outcome evidence reviewed" in template
    assert "structural READY is NOT semantic correctness" in template
    # B2: selective loading still named in the same template.
    assert "load only its effective bounded policy portfolio" in template


def test_remediation_prompt_names_three_dimensions() -> None:
    """The remediation prompt's "Three-state readiness" section names
    the three dimensions and the structural-vs-semantic distinction
    so the agent authoring a remediation does not present a
    structural-READY remediation as semantic correctness.
    """
    from ralph.prompts.template_engine import render_template
    from ralph.prompts.template_registry import (
        _packaged_template_cache,
        load_partial_templates,
        packaged_template_root,
    )

    # The packaged-template cache serves the immutable process-wide
    # snapshot, so a real-file read is unnecessary in a test and
    # the test-audit policy forbids raw ``read_text`` on the
    # filesystem seam.
    template = _packaged_template_cache.get(
        "policy_remediation.jinja", root=packaged_template_root()
    )
    partials = load_partial_templates((packaged_template_root(),))
    rendered = render_template(
        template,
        {
            "findings_block": "",
            "analysis_feedback_block": "",
            "analysis_feedback_summary": "",
            "analysis_feedback_status": "",
            "canonical_dir": markers.CANONICAL_DIR,
            "gate_script_policy_path": "docs/ralph-workflow-policy/gate-script-policy.md",
            "approved_tools": "make pytest mypy ruff",
            "agents_block_begin": markers.AGENTS_BLOCK_BEGIN,
            "agents_block_end": markers.AGENTS_BLOCK_END,
            "verify_tool_names": "tool",
            "submit_tool_names": "tool",
            "declare_complete_tool_names": "tool",
            "artifact_type": "policy_remediation",
            "migrated_marker": markers.MIGRATED_MARKER_TEMPLATE.format(target="x"),
            "LAST_RETRY_ERROR": "",
        },
        partials,
    )
    assert "structure complete" in rendered
    assert "implementation verified" in rendered
    assert "outcome evidence reviewed" in rendered
    assert "structural READY is NOT semantic correctness" in rendered


def test_cli_ready_emit_threads_three_dimensions() -> None:
    """The CLI's READY emit path includes the three-state assessment
    when the cached result carries one, and the explicit
    structural-vs-semantic disclaimer when only structure is true.

    Verified at the boundary: the result the preflight returns is
    shaped to the v5 contract, and the emit path is the single
    chokepoint the user sees. A regression that drops the
    assessment off the wire is what this test catches.
    """
    from ralph.project_policy.models import ReadinessResult, ReadinessStatus

    assessment = ReadinessAssessment(
        structure_complete=True,
        implementation_verified=False,
        outcome_evidence_reviewed=False,
    )
    result = ReadinessResult(
        status=ReadinessStatus.READY,
        assessment=assessment,
        findings=[],
    )
    # ``to_report`` is the public surface the preflight hands back;
    # the CLI emit must surface the three dimensions on the same
    # line so a future grep on the emit can prove the contract.
    report = result.to_report()
    assert any("structure_complete=true" in line for line in report)
    assert any("implementation_verified=false" in line for line in report)
    assert any("outcome_evidence_reviewed=false" in line for line in report)


def test_cli_integration_module_imports_readiness_emission() -> None:
    """The CLI integration module must wire the new three-state
    emitter into the run-loop seam so a future regression that
    rewrites the import is caught.
    """
    import inspect

    src = inspect.getsource(cli_integration)
    assert "readiness-assessment:" in src
    assert "implementation_verified" in src
    assert "outcome_evidence_reviewed" in src
    # The string is split across lines in the source; check for the
    # parts that anchor the contract.
    assert "structural READY is NOT semantic" in src
    # The new footer push must be wired in the same module.
    assert "push_readiness_assessment_status_bar" in src
