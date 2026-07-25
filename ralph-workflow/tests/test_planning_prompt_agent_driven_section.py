"""The new ``## Agent-Driven Parallel Execution`` heading must be present
in ``planning.jinja`` and the old
``## Same-Workspace Parallel Worker Rules`` heading must be absent.

This test reads the template source directly (rather than rendering it
through the custom template engine) because the source-text checks are
exactly what the audit (``audit_parallelization_dormant``) enforces on
the bundled prompt — a drift in the rendered prompt always means a drift
in the source text.
"""

from __future__ import annotations

from pathlib import Path

_PLANNING_TEMPLATE = (
    Path(__file__).resolve().parents[1] / "ralph" / "prompts" / "templates" / "planning.jinja"
)
_PLANNING_ANALYSIS_TEMPLATE = (
    Path(__file__).resolve().parents[1]
    / "ralph"
    / "prompts"
    / "templates"
    / "planning_analysis.jinja"
)


def _read_planning_template() -> str:
    return _PLANNING_TEMPLATE.read_text(encoding="utf-8")


def test_planning_prompt_contains_new_heading_and_lacks_old() -> None:
    source = _read_planning_template()
    assert "## Agent-Driven Parallel Execution" in source, (
        "planning prompt must include the new agent-driven section"
    )
    assert "## Same-Workspace Parallel Worker Rules" not in source, (
        "planning prompt must NOT include the legacy same-workspace section"
    )


def test_planning_prompt_new_section_warns_about_fan_out() -> None:
    source = _read_planning_template()
    normalized = " ".join(source.split())
    # The thinking-first rewrite compresses the eight-rule list to the rules
    # the format doc does not already carry; the dormant-fan-out sentence is
    # the executor-facing pointer the agent needs in the prompt itself.
    assert "Ralph-managed worker fan-out is dormant" in normalized
    assert "sub-agents" in source
    assert "ralph coordinate" not in source, (
        "planning prompt must not reference the nonexistent ralph coordinate "
        "command, even as a prohibition"
    )


def test_planning_prompt_distinguishes_agent_subagents_from_ralph_workers() -> None:
    source = _read_planning_template()
    normalized = " ".join(source.split())

    assert "agent_subagents" in source
    assert "ralph_fan_out" in source
    assert (
        "Agent-managed sub-agents return implementation and proof to the main "
        "execution session"
        in normalized
    )
    assert (
        "Ralph-managed worker fan-out is dormant in the bundled default"
        in normalized
    )
    assert "Ralph-managed coordination is not a path" not in source
    assert "the only active path for parallel plan execution" not in source


def test_planning_prompts_distinguish_work_units_from_execution_subplans() -> None:
    source = _read_planning_template()
    analysis_source = _PLANNING_ANALYSIS_TEMPLATE.read_text(encoding="utf-8")

    for text in (source, analysis_source):
        normalized = " ".join(text.split())
        assert "Work Units" in normalized
        assert "small bounded tasks or verification gates" in normalized
        # The two prompts use slightly different casing for the
        # execution-subagent mini-plan phrase; both phrasings exist in the
        # templates after the thinking-first rewrite.
        assert (
            "execution sub-agent gets a complete mini-plan" in normalized
            or "execution-subagent mini-plan" in normalized
        )
        assert "four or five independent execution Subplans" in normalized
        assert "main-session" in normalized
    assert "specific `Expect:`" in source or "Expect: <" in source
    assert "every `Verify:` command is paired with a specific" in analysis_source


def test_planning_prompts_use_author_facing_markdown_labels() -> None:
    source = _read_planning_template()
    analysis_source = _PLANNING_ANALYSIS_TEMPLATE.read_text(encoding="utf-8")
    combined = source + analysis_source

    # The thinking-first rewrite makes ``## Critical Files`` a recommendation
    # the executor may skip when the work is small enough; we still require
    # the fan-out headings and ``Directories:`` because those are the words
    # the worker fan-out parses for.
    for label in ("## Parallel Plan", "## Work Units", "Directories:"):
        assert label in combined, f"missing label {label!r}"
    # ``Critical Files`` is mentioned by name in the document-contract
    # paragraph even when the prompt no longer hard-requires it.
    assert "Critical Files" in combined
    for internal_name in (
        "critical_files",
        "summary.coverage_areas",
        "summary.intent",
        "work_units",
        "parallel_plan",
        "allowed_directories",
        "edit_area",
        "expected_evidence",
        "verify_command",
        "plan_items_proven",
        "unit_id",
        "Pydantic model is the source of truth",
        "JSON Schema for the plan artifact",
    ):
        assert internal_name not in combined
    assert ".agent" in source
    assert ".git" in source


def test_planning_prompt_preserves_project_specific_step_vocabularies() -> None:
    source = _read_planning_template()
    normalized = " ".join(source.split())

    assert "Project-specific `Type:` values are accepted" in normalized
    assert "preserved verbatim" in normalized
    assert "normalize to `action`" not in source
    assert "Use only canonical step types" not in source


def test_planning_prompt_teaches_fail_closed_fan_out_headings() -> None:
    normalized = " ".join(_read_planning_template().split())

    # Thinking-first rewrite compresses the eight-rule list to the rules
    # the format doc does not already carry; fail-closed unit-marker parsing
    # is in the format doc, so the planning prompt only points at it.
    assert "Work Units" in normalized
    assert "Parallel Plan" in normalized
    assert "Acceptance-criterion items" in normalized or "criteria, not work units" in normalized
