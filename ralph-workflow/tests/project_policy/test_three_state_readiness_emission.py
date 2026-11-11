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
    a = ReadinessAssessment()
    assert a.structure_complete is False
    assert a.implementation_verified is False
    assert a.outcome_evidence_reviewed is False


def test_readiness_footer_label_renders_three_states() -> None:
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
    assert "S*" in label_mixed
    assert "I-" in label_mixed
    assert "O-" in label_mixed


def test_agents_md_ready_template_names_three_dimensions() -> None:
    template = agents_md._AGENTS_READY_TEMPLATE.format(
        begin=markers.AGENTS_BLOCK_BEGIN,
        end=markers.AGENTS_BLOCK_END,
        canonical_dir=markers.CANONICAL_DIR,
    )
    assert "structure complete" in template
    assert "implementation verified" in template
    assert "outcome evidence reviewed" in template
    assert "structural READY is NOT semantic correctness" in template
    assert "load only its effective bounded policy portfolio" in template


def test_remediation_prompt_names_three_dimensions() -> None:
    from ralph.prompts.template_engine import render_template
    from ralph.prompts.template_registry import (
        _packaged_template_cache,
        load_partial_templates,
        packaged_template_root,
    )

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
    report = result.to_report()
    assert any("structure_complete=true" in line for line in report)
    assert any("implementation_verified=false" in line for line in report)
    assert any("outcome_evidence_reviewed=false" in line for line in report)


def test_cli_integration_module_imports_readiness_emission() -> None:
    import inspect

    src = inspect.getsource(cli_integration)
    assert "readiness-assessment:" in src
    assert "implementation_verified" in src
    assert "outcome_evidence_reviewed" in src
    assert "structural READY is NOT semantic" in src
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
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# User intro\n")
    agents_md.bootstrap(ws)

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

    ws2 = MemoryWorkspace()
    ws2.write(markers.AGENTS_MD, "# User intro\n")
    agents_md.bootstrap(ws2)
    agents_md.condense_placeholder_block(ws2, assessment=None)
    content2 = ws2.read(markers.AGENTS_MD)
    assert "structure complete=unknown" in content2
    assert "implementation verified=unknown" in content2
    assert "outcome evidence reviewed=unknown" in content2


def test_agents_md_condense_refreshes_dimensions_after_first_call() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.AGENTS_MD, "# User intro\n")
    agents_md.bootstrap(ws)

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
    ws = MemoryWorkspace()
    assessment = ReadinessAssessment(
        structure_complete=True,
        implementation_verified=True,
        outcome_evidence_reviewed=False,
    )
    rem_prompt = remediation._render_prompt([], workspace=ws, assessment=assessment)
    assert "structure_complete=true" in rem_prompt
    assert "implementation_verified=true" in rem_prompt
    assert "outcome_evidence_reviewed=false" in rem_prompt

    rem_prompt_none = remediation._render_prompt([], workspace=ws, assessment=None)
    assert "structure_complete=unknown" in rem_prompt_none
    assert "implementation_verified=unknown" in rem_prompt_none
    assert "outcome_evidence_reviewed=unknown" in rem_prompt_none

    analysis_prompt = analysis._render_prompt(ws, assessment=assessment)
    assert "structure_complete=true" in analysis_prompt
    assert "implementation_verified=true" in analysis_prompt
    assert "outcome_evidence_reviewed=false" in analysis_prompt

    analysis_prompt_none = analysis._render_prompt(ws, assessment=None)
    assert "structure_complete=unknown" in analysis_prompt_none
    assert "implementation_verified=unknown" in analysis_prompt_none
    assert "outcome_evidence_reviewed=unknown" in analysis_prompt_none
