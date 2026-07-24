"""The new ``## PARALLEL EXECUTION`` section must be present in
``developer_iteration.jinja`` and must follow the expected contract.

This test reads the template source directly (rather than rendering it
through the custom template engine) because the source-text checks are
exactly what the audit (``audit_parallelization_dormant``) enforces on
the bundled prompt — a drift in the rendered prompt always means a drift
in the source text.
"""

from __future__ import annotations

from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates"
_DEVELOPER_TEMPLATE = _TEMPLATES_DIR / "developer_iteration.jinja"
_FALLBACK_TEMPLATE = _TEMPLATES_DIR / "developer_iteration_fallback.jinja"
_FIX_TEMPLATE = _TEMPLATES_DIR / "fix_mode.jinja"
_WORKER_TEMPLATE = _TEMPLATES_DIR / "worker_developer.jinja"
# The PARALLEL EXECUTION body is shared verbatim with the continuation
# template via this partial; the heading itself stays in the template.
_PARALLEL_EXECUTION_PARTIAL = _TEMPLATES_DIR / "shared" / "_parallel_execution.jinja"


def _read_developer_template() -> str:
    return "\n".join(
        (
            _DEVELOPER_TEMPLATE.read_text(encoding="utf-8"),
            _PARALLEL_EXECUTION_PARTIAL.read_text(encoding="utf-8"),
        )
    )


def test_developer_prompt_includes_parallel_execution_section() -> None:
    source = _read_developer_template()
    assert "## PARALLEL EXECUTION" in source
    assert "## Work Units" in source
    assert "sub-agents" in source


def test_developer_prompt_never_references_phantom_coordinate_command() -> None:
    """``ralph coordinate`` does not exist in the Python CLI; prompts must not
    mention it, even as a prohibition — agents should instead be told that no
    coordination command exists at all."""
    source = _read_developer_template()
    assert "ralph coordinate" not in source
    assert "no coordination command" in source


def test_developer_prompt_section_tells_executor_to_dispatch_subagents() -> None:
    source = _read_developer_template()
    assert "responsible for dispatching your own sub-agents" in source
    assert "If your agent runtime does not support sub-agents" in source
    assert "execute the same plan sequentially" in source


def test_developer_prompt_encourages_proactive_subagent_use() -> None:
    """Sub-agents are not limited to declared work units: the developer may
    parallelize information gathering and naturally concurrent steps."""
    source = _read_developer_template()
    assert "not limited to declared work units" in source
    assert "Information gathering" in source
    assert "Naturally concurrent steps" in source
    assert "When to stay sequential" in source
    assert "Never let two sub-agents edit the same file" in source


def test_developer_prompt_executes_tiny_linear_plans_without_delegation_overhead() -> None:
    source = _read_developer_template()
    assert "For a compact, linear plan with only a few tightly related steps" in source
    assert "execute it directly in the main session" in source
    assert "Do not create delegation overhead" in source


def test_developer_prompt_assigns_independent_units_to_disjoint_subagents() -> None:
    source = _read_developer_template()
    assert "multiple subplans" in source
    assert "dedicated sub-agent for each independent unit" in source
    assert "Assign disjoint file ownership before dispatch" in source
    assert "respect explicit dependency ordering" in source
    assert "Do not duplicate work between units" in source
    assert "common cues, not required plan structure" in source


def test_developer_prompt_fans_out_and_fans_in_four_to_five_units() -> None:
    source = _read_developer_template()
    assert "For 4-5 units" in source
    assert "dispatch every independent ready unit in parallel" in source
    assert "collect each unit's proof" in source
    assert "fan in" in source
    assert "integrate the combined result" in source
    assert "cross-unit verification" in source
    assert "final acceptance verification" in source


def test_fallback_and_fix_prompts_reuse_shape_guidance_before_artifact_contract() -> None:
    cases = (
        (_FALLBACK_TEMPLATE, "## Development result artifact contract"),
        (_FIX_TEMPLATE, "## Fix result artifact contract"),
    )
    for template_path, artifact_heading in cases:
        source = template_path.read_text(encoding="utf-8")
        include = "{% include 'shared/_parallel_execution.j2' %}"
        assert include in source
        assert source.index(include) < source.index(artifact_heading)


def test_worker_scope_override_follows_base_context_and_forbids_whole_plan_work() -> None:
    source = _WORKER_TEMPLATE.read_text(encoding="utf-8")
    override_heading = "## WORKER SCOPE OVERRIDE"
    assert source.index("{{ base_prompt }}") < source.index(override_heading)
    assert source.index(override_heading) < source.index("render_artifact_submission(")
    assert "implement ONLY the assigned work unit `{{ unit_id }}`" in source
    assert "Do not execute, coordinate, integrate, or verify the whole plan" in source
    assert "Do not work on another unit or duplicate its work" in source
    assert "exactly one `- [{{ unit_id }}]` item" in source
    assert "No other plan-step or work-unit proof IDs are allowed" in source


def test_developer_prompt_keeps_development_result_block_intact() -> None:
    """Sanity check: the surrounding prompt still includes the
    DEVELOPMENT RESULT ARTIFACT block (we must not have removed it
    when inserting the PARALLEL EXECUTION block).
    """
    source = _read_developer_template()
    assert "## DEVELOPMENT RESULT ARTIFACT" in source
    assert "## PARALLEL EXECUTION" in source
    assert source.index("## PARALLEL EXECUTION") < source.index("## DEVELOPMENT RESULT ARTIFACT")
