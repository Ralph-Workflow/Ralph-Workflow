"""Planning templates teach one compact executor-ready contract."""

from __future__ import annotations

from pathlib import Path

from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace


def _source(name: str) -> str:
    return TemplateContext.default().registry.get_template(name.removesuffix(".jinja"))


def test_planning_variants_share_thinking_and_submission_partials() -> None:
    for name in (
        "planning.jinja",
        "planning_fallback.jinja",
        "planning_edit.jinja",
        "planning_edit_fallback.jinja",
    ):
        source = _source(name)
        assert "shared/_planning_thinking.j2" in source
        assert "shared/_planning_submission_mechanics.j2" in source


def test_thinking_partial_uses_evidence_and_the_four_work_phases() -> None:
    source = _source("shared/_planning_thinking.jinja")

    for phase in ("Orient", "Characterize", "Change", "Verify"):
        assert phase in source
    assert "Inspect the repository before naming paths or commands" in source
    assert "discovery step for an honest unknown" in source


def test_planning_prompt_submission_guidance_points_at_partition() -> None:
    """S-3: planning.jinja's submission guidance must reference the Partition
    step so the planner reads the partition heuristic before submitting.
    """
    source = _source("planning.jinja")

    # Submission section names the Partition step and the Work Units format.
    assert "Partition" in source
    assert "Work Units" in source


def test_submission_partial_names_the_mandatory_contract() -> None:
    source = _source("shared/_planning_submission_mechanics.j2")

    assert ".agent/artifact-formats/plan.md" not in source
    assert "Stable `### [S-n] Title` steps are recommended" in source
    assert "Frontmatter and step blocks are optional" in source
    assert "Only binary control characters are rejected" in source


def test_submission_partial_renders_policy_derived_unit_cap(tmp_path: Path) -> None:
    """S-8: the planning prompt states the policy-derived ``## Work Units`` cap.

    The cap is read from the development phase's ``max_work_units``
    (the per-workspace worker ceiling) rather than hard-coded; the
    template receives it as ``WORK_UNITS_MAX_CAP`` and renders it as
    part of the submission mechanics so the planner plans around the
    actual limit instead of guessing.
    """
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Author a parallel plan with Work Units")
    policy = load_policy(tmp_path / ".agent")
    cap = policy.pipeline.phases["development"].parallelization.max_work_units

    path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.PLANNING),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(
            artifacts_policy=policy.artifacts,
        ),
    )
    rendered = workspace.read(path)

    assert f"cap of {cap}" in rendered
    assert "limits concurrent workers, not total units" in rendered


def test_planning_analysis_renders_configured_unit_cap(tmp_path: Path) -> None:
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Validate a parallel plan.")
    workspace.write(".agent/PLAN.md", "## Summary\nValidate the plan.\n")
    policy = load_policy(tmp_path / ".agent")
    development = policy.pipeline.phases["development"]
    assert development.parallelization is not None
    parallelization = development.parallelization.model_copy(
        update={"max_parallel_workers": 2, "max_work_units": 13}
    )
    phases = dict(policy.pipeline.phases)
    phases["development"] = development.model_copy(update={"parallelization": parallelization})
    pipeline = policy.pipeline.model_copy(update={"phases": phases})

    path = materialize_prompt_for_phase(
        PromptPhaseContext(
            phase="planning_analysis",
            workspace=workspace,
            pipeline_policy=pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.ANALYSIS),
            workspace_root=tmp_path,
        ),
        PromptPhaseOptions(artifacts_policy=policy.artifacts),
    )

    assert "cap" in workspace.read(path)
    assert "of 13" in workspace.read(path)
    assert "of 8" not in workspace.read(path)


def test_submission_partial_keeps_work_units_grammar_compact() -> None:
    """S-8: the partial documents the Work Units syntax compactly.

    The format-doc tells the planner the exact bracket and field shape
    (``[U-1]`` items, ``Directories:`` + optional ``Paths:``,
    ``Depends on:``, nested ``### [S-n]`` steps, the disjoint-directory
    constraint, the no-reserved-paths rule, and the Work Units XOR
    Parallel Plan exclusivity). The submission mechanics render the
    cap; the format doc is where the planner reads the syntax.
    """
    from ralph.mcp.artifacts.format_docs import load_bundled_format_doc

    source = load_bundled_format_doc("plan")
    assert source is not None

    assert "## Work Units" in source
    assert "- [U-1]" in source
    assert "Directories:" in source
    assert "Depends on:" in source
    assert ".agent" in source
    assert ".git" in source
    assert ".worktrees" in source
    # Mutually exclusive with the legacy `## Parallel Plan` form.
    assert "Parallel Plan" in source
