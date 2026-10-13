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

from pathlib import Path

import pytest

from ralph.project_policy import (
    agents_md,
    analysis,
    cli_integration,
    markers,
    remediation,
)
from ralph.project_policy._readiness_assessment import ReadinessAssessment
from ralph.project_policy.status_bar import format_readiness_footer_label
from ralph.workspace.memory import MemoryWorkspace
from ralph.workspace.scope import WorkspaceScope


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


@pytest.mark.parametrize(
    ("sc", "iv", "oer", "expected_dims", "expected_note_fragment", "forbidden_note_fragment"),
    [
        (
            True,
            True,
            False,
            "structure_complete=true, implementation_verified=true, outcome_evidence_reviewed=false",
            "outcome evidence reviewed is still false",
            "implementation verified",
        ),
        (
            True,
            False,
            True,
            "structure_complete=true, implementation_verified=false, outcome_evidence_reviewed=true",
            "implementation verified is still false",
            "outcome evidence reviewed",
        ),
        (
            True,
            False,
            False,
            "structure_complete=true, implementation_verified=false, outcome_evidence_reviewed=false",
            "implementation verified and outcome evidence reviewed are still false",
            None,
        ),
        (
            True,
            True,
            True,
            "structure_complete=true, implementation_verified=true, outcome_evidence_reviewed=true",
            None,
            "readiness-assessment-note",
        ),
        (
            False,
            False,
            False,
            "structure_complete=false, implementation_verified=false, outcome_evidence_reviewed=false",
            None,
            "readiness-assessment-note",
        ),
    ],
)
def test_emit_readiness_assessment_derives_truthful_notes(
    sc: bool,
    iv: bool,
    oer: bool,
    expected_dims: str,
    expected_note_fragment: str | None,
    forbidden_note_fragment: str | None,
) -> None:
    """Observable CLI emit: the readiness assessment note must name ONLY the
    dimensions that are actually false.
    """
    emitted: list[str] = []
    scope = WorkspaceScope(Path())
    assessment = ReadinessAssessment(
        structure_complete=sc,
        implementation_verified=iv,
        outcome_evidence_reviewed=oer,
    )
    cli_integration._emit_and_push_readiness_assessment(
        emitted.append,
        display=None,
        workspace_scope=scope,
        assessment=assessment,
        push_status=False,
    )
    assert any(expected_dims in line for line in emitted)
    if expected_note_fragment is not None:
        note_line = next(line for line in emitted if "readiness-assessment-note:" in line)
        assert expected_note_fragment in note_line
        if forbidden_note_fragment is not None:
            assert forbidden_note_fragment not in note_line
    else:
        assert not any("readiness-assessment-note:" in line for line in emitted)


def test_agents_md_condense_reports_dimension_values() -> None:
    """The condensed AGENTS.md block reports current dimension values."""
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# User intro\n")
    agents_md.bootstrap(ws)

    # Condense with explicit mixed assessment:
    assessment = ReadinessAssessment(
        structure_complete=True,
        implementation_verified=True,
        outcome_evidence_reviewed=False,
    )
    agents_md.condense_placeholder_block(ws, assessment=assessment)
    content = ws.read(markers.AGENTS_MD)
    assert "structure complete=true" in content
    assert "implementation verified=true" in content
    assert "outcome evidence reviewed=false" in content

    # Condense with None assessment:
    ws2 = MemoryWorkspace()
    ws2.write(markers.AGENTS_MD, "# User intro\n")
    agents_md.bootstrap(ws2)
    agents_md.condense_placeholder_block(ws2, assessment=None)
    content2 = ws2.read(markers.AGENTS_MD)
    assert "structure complete=unknown" in content2
    assert "implementation verified=unknown" in content2
    assert "outcome evidence reviewed=unknown" in content2


def test_agents_md_condense_refreshes_dimensions_after_first_call() -> None:
    """Subsequent condense calls refresh the three readiness dimensions.

    Reproduces the v5 readiness refresh regression: after a first
    condense with all-true dimensions, a later assessment that flips
    implementation_verified and outcome_evidence_reviewed to false
    must update the on-disk block; previously the second call was a
    no-op because the placeholder sentinel had already been removed.
    """
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# User intro\n")
    agents_md.bootstrap(ws)

    # First condense: all true.
    first = agents_md.condense_placeholder_block(
        ws,
        assessment=ReadinessAssessment(
            structure_complete=True,
            implementation_verified=True,
            outcome_evidence_reviewed=True,
        ),
    )
    assert first == [markers.AGENTS_MD]
    content = ws.read(markers.AGENTS_MD)
    assert "structure complete=true" in content
    assert "implementation verified=true" in content
    assert "outcome evidence reviewed=true" in content

    # Second condense: implementation and outcome go false — must refresh.
    second = agents_md.condense_placeholder_block(
        ws,
        assessment=ReadinessAssessment(
            structure_complete=True,
            implementation_verified=False,
            outcome_evidence_reviewed=False,
        ),
    )
    assert second == [markers.AGENTS_MD]
    refreshed = ws.read(markers.AGENTS_MD)
    assert "structure complete=true" in refreshed
    assert "implementation verified=false" in refreshed
    assert "outcome evidence reviewed=false" in refreshed
    assert "implementation verified=true" not in refreshed
    assert "outcome evidence reviewed=true" not in refreshed

    # Idempotent guard: a third call with the same assessment is a no-op.
    third = agents_md.condense_placeholder_block(
        ws,
        assessment=ReadinessAssessment(
            structure_complete=True,
            implementation_verified=False,
            outcome_evidence_reviewed=False,
        ),
    )
    assert third == []
    assert ws.read(markers.AGENTS_MD) == refreshed

    # User-authored block (no Ralph-managed signature) is preserved.
    ws_user = MemoryWorkspace()
    custom = (
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"Read the policies under {markers.CANONICAL_DIR} first.\n"
        f"{markers.AGENTS_BLOCK_END}\n"
    )
    ws_user.write(markers.AGENTS_MD, custom)
    assert (
        agents_md.condense_placeholder_block(
            ws_user,
            assessment=ReadinessAssessment(True, False, False),
        )
        == []
    )
    assert ws_user.read(markers.AGENTS_MD) == custom


def test_remediation_and_analysis_prompts_render_dimension_values() -> None:
    """Remediation and analysis prompts report current dimension values."""
    ws = MemoryWorkspace()
    assessment = ReadinessAssessment(
        structure_complete=True,
        implementation_verified=True,
        outcome_evidence_reviewed=False,
    )
    # Remediation prompt with assessment:
    rem_prompt = remediation._render_prompt([], workspace=ws, assessment=assessment)
    assert "structure_complete=true" in rem_prompt
    assert "implementation_verified=true" in rem_prompt
    assert "outcome_evidence_reviewed=false" in rem_prompt

    # Remediation prompt without assessment (defaults to unknown):
    rem_prompt_none = remediation._render_prompt([], workspace=ws, assessment=None)
    assert "structure_complete=unknown" in rem_prompt_none
    assert "implementation_verified=unknown" in rem_prompt_none
    assert "outcome_evidence_reviewed=unknown" in rem_prompt_none

    # Analysis prompt with assessment:
    analysis_prompt = analysis._render_prompt(ws, assessment=assessment)
    assert "structure_complete=true" in analysis_prompt
    assert "implementation_verified=true" in analysis_prompt
    assert "outcome_evidence_reviewed=false" in analysis_prompt

    # Analysis prompt without assessment:
    analysis_prompt_none = analysis._render_prompt(ws, assessment=None)
    assert "structure_complete=unknown" in analysis_prompt_none
    assert "implementation_verified=unknown" in analysis_prompt_none
    assert "outcome_evidence_reviewed=unknown" in analysis_prompt_none
