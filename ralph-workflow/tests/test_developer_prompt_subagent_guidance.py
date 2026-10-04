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


# ---------------------------------------------------------------------------
# S-4: ready group, per-unit brief checklist, and budget-overflow dispatch
# ---------------------------------------------------------------------------


def test_developer_prompt_defines_independent_ready_group_for_linear_plans() -> None:
    """S-4(a): a linear plan with no `Depends on:` path between any pair and
    pairwise disjoint `Files:` lists forms an independent ready group; budget
    pressure escalates the group to a concurrent dispatch rather than
    trimming scope.
    """
    source = _read_developer_template()
    flat = " ".join(source.split())
    # Ready group definition.
    assert "ready group" in source
    assert "no" in source and "Depends on:" in source
    # Budget-overflow dispatch (no scope trimming).
    assert "remaining budget" in source
    assert "cannot fit" in source
    assert "concurrently rather than trimming" in flat


def test_developer_prompt_per_unit_brief_checklist_is_present() -> None:
    """S-4(b): the per-unit subagent brief carries unit ID, allowed dirs,
    brokered-MCP-only tools, no git / no out-of-scope writes, focused verify,
    and the exact return format.
    """
    source = _read_developer_template()

    # Brief checklist headers.
    assert "Unit ID" in source
    assert "allowed directories" in source
    assert "brokered MCP tools" in source
    # Prohibitions.
    assert "no git" in source or "No git" in source
    assert "outside the unit" in source or "outside scope" in source
    # Verify contract.
    assert "focused" in source.lower()
    # Return format fields.
    assert "files touched" in source or "files changed" in source
    assert "exit code" in source
    assert "reprodu" in source  # reproduce / reproduction


def test_developer_prompt_names_dispatching_parallel_agents_skill() -> None:
    """S-4(c): when units exist, the prompt points at the
    dispatching-parallel-agents skill as the reference.
    """
    source = _read_developer_template()
    assert "dispatching-parallel-agents" in source


# ---------------------------------------------------------------------------
# S-6: first-iteration coverage check and every-iteration pre-submit review
# ---------------------------------------------------------------------------


def test_developer_prompt_first_iteration_maps_request_to_plan_steps() -> None:
    """S-6(a): on the first iteration, the developer prompt must require a
    request-versus-plan coverage check before implementing, mapping every
    concrete deliverable to a plan step (or recording a continuation gap).
    """
    source = _read_developer_template()
    assert "coverage check" in source.lower() or "map every" in source.lower()
    assert "request" in source.lower()
    assert "plan step" in source.lower() or "plan items" in source.lower()
    # Continuation-gap recording.
    assert "continuation" in source.lower() or "next step" in source.lower()


def test_developer_prompt_has_pre_submit_review_on_every_iteration() -> None:
    """S-6(a): the cheap independent pre-submit review must run on every
    iteration, not only continuations — the continuation template already
    carries the rule.
    """
    source = _read_developer_template()
    assert "Before submitting" in source or "before submitting" in source.lower()
    assert "review" in source.lower() or "check" in source.lower()
    # Parallel ordering: PARALLEL EXECUTION precedes DEVELOPMENT RESULT.
    assert source.index("## PARALLEL EXECUTION") < source.index("## DEVELOPMENT RESULT ARTIFACT")
