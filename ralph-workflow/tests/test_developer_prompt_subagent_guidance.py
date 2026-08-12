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


def test_developer_prompt_section_tells_executor_to_dispatch_subagents() -> None:
    source = _read_developer_template()
    assert "dispatch ready units concurrently" in source
    assert "If sub-agents are unavailable" in source
    assert "execute\nunits sequentially" in source


def test_developer_prompt_limits_parallel_edits_to_disjoint_units() -> None:
    source = _read_developer_template()
    assert "independent units" in source
    assert "disjoint file ownership" in source
    assert "Never let two agents edit the same file" in source


def test_developer_prompt_executes_tiny_linear_plans_without_delegation_overhead() -> None:
    source = _read_developer_template()
    assert "compact or coupled steps" in source
    assert "Execute" in source
    assert "main session" in source


def test_developer_prompt_assigns_independent_units_to_disjoint_subagents() -> None:
    source = _read_developer_template()
    assert "plan declares independent units" in source
    assert "dispatch ready units concurrently" in source
    assert "Respect dependencies" in source


def test_developer_prompt_fans_out_and_fans_in_independent_units() -> None:
    source = _read_developer_template()
    assert "collect each unit's" in source
    assert "integrate" in source
    assert "cross-unit and" in source
    assert "full verification" in source


def test_fallback_prompt_reuses_shape_guidance_before_artifact_contract() -> None:
    source = _FALLBACK_TEMPLATE.read_text(encoding="utf-8")
    include = "{% include 'shared/_parallel_execution.j2' %}"
    assert include in source
    assert source.index(include) < source.index("## Development result artifact contract")


def test_worker_scope_override_follows_base_context_and_forbids_whole_plan_work() -> None:
    source = _WORKER_TEMPLATE.read_text(encoding="utf-8")
    scope_heading = "## WORKER SCOPE"
    assert source.index(scope_heading) < source.index("render_artifact_submission(")
    assert "Implement and verify only `{{ unit_id }}`" in source
    assert "coordinate other units, integrate the whole plan" in source
    assert "exactly one proof item" in source
    assert "`- [{{ unit_id }}]`" in source


def test_developer_prompt_keeps_development_result_block_intact() -> None:
    """Sanity check: the surrounding prompt still includes the
    DEVELOPMENT RESULT ARTIFACT block (we must not have removed it
    when inserting the PARALLEL EXECUTION block).
    """
    source = _read_developer_template()
    assert "## DEVELOPMENT RESULT ARTIFACT" in source
    assert "## PARALLEL EXECUTION" in source
    assert source.index("## PARALLEL EXECUTION") < source.index("## DEVELOPMENT RESULT ARTIFACT")
