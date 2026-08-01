"""Tests for ``ralph.testing.audit_template_render_integrity``.

The audit renders every packaged top-level prompt template and every shared
partial through the real rendering path (registry + partials +
``TemplateRenderer``) across reachable capability profiles and explicit
optional-input scenarios. It enforces five render-integrity checks: no
unrendered Jinja markers, include resolution, no duplicated headings, no
duplicated >=120-char paragraphs, and no blank-line/doubled-label defects.

The clean-template render check (``test_audit_clean_on_current_templates``)
used to live here as the verify-gate wiring, but it duplicates the
``audit_template_render_integrity`` step in ``_VERIFY_STEPS`` so it was
removed in the wt-05-test-opti pass. The remaining fixture-driven tests
below cover every audit branch on synthetic inputs without touching the
packaged templates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import ralph.testing.audit_template_render_integrity as audit_module
from ralph.prompts.template_context import TemplateContext
from ralph.testing.audit_template_render_integrity import (
    _branch_scenarios,
    _conditional_variable_groups,
    _conditional_variable_names,
    _render_targets,
    check_rendered_prompt,
)
from ralph.testing.audit_template_render_integrity import main as audit_main

if TYPE_CHECKING:
    import pytest

_CLEAN_PROMPT = (
    "# Title\n\n"
    "## Section A\n\n"
    "Some body text.\n\n"
    "```bash\n# a shell comment\n# a shell comment\n```\n\n"
    "## Section B\n\n"
    "LABEL:\ncontent\n"
)


def test_clean_prompt_produces_no_violations() -> None:
    assert check_rendered_prompt("example", _CLEAN_PROMPT) == []


def test_detects_unrendered_jinja_markers() -> None:
    descriptions = check_rendered_prompt("example", "Body with {{ LEFTOVER }} and {% if x %}.")
    assert any("'{{'" in d for d in descriptions)
    assert any("'{%'" in d for d in descriptions)


def test_detects_duplicated_heading_outside_code_fences() -> None:
    rendered = "## Steps\n\nbody\n\n## Steps\n"
    descriptions = check_rendered_prompt("example", rendered)
    assert any("duplicated heading" in d and "## Steps" in d for d in descriptions)


def test_ignores_repeated_hash_lines_inside_code_fences() -> None:
    rendered = "## Steps\n\n```\n## Steps\n## Steps\n```\n"
    assert check_rendered_prompt("example", rendered) == []


def test_detects_duplicated_long_paragraph() -> None:
    paragraph = ("restated guidance sentence " * 6).strip()
    assert len(paragraph) >= 120
    rendered = f"intro\n\n{paragraph}\n\nmiddle\n\n{paragraph}\n"
    descriptions = check_rendered_prompt("example", rendered)
    assert any("duplicated paragraph" in d for d in descriptions)


def test_short_repeated_fragments_are_not_flagged() -> None:
    rendered = "intro\n\nshort repeated line\n\nmiddle\n\nshort repeated line\n"
    assert check_rendered_prompt("example", rendered) == []


def test_detects_blank_line_runs_of_three_or_more() -> None:
    descriptions = check_rendered_prompt("example", "a\n\n\n\nb\n")
    assert any("consecutive blank lines" in d for d in descriptions)
    assert check_rendered_prompt("example", "a\n\n\nb\n") == []


def test_ignores_blank_line_runs_inside_code_fences() -> None:
    rendered = "before\n\n```markdown\nfirst\n\n\n\nsecond\n```\n\nafter"
    assert check_rendered_prompt("example", rendered) == []


def test_detects_doubled_label_line() -> None:
    descriptions = check_rendered_prompt(
        "example", "ANALYSIS FEEDBACK:\nANALYSIS FEEDBACK:\nbody\n"
    )
    assert any("doubled label line" in d and "ANALYSIS FEEDBACK:" in d for d in descriptions)


def test_condition_discovery_covers_uppercase_and_lowercase_names() -> None:
    sources = {
        "sample": (
            "{% if UPPER_FLAG %}upper{% endif %}"
            "{% if lower_flag|default(false) %}lower{% endif %}"
            "{% if mode|default('') == 'planning' %}planning{% endif %}"
        )
    }

    assert _conditional_variable_names(sources) == {
        "UPPER_FLAG",
        "lower_flag",
        "mode",
    }


def test_condition_discovery_combines_ancestor_and_nested_branch_inputs() -> None:
    groups = _conditional_variable_groups(
        {
            "sample": (
                "{% if PRIOR_RESULT_STATUS %}"
                "{% if IS_WORKER %}worker{% else %}parent{% endif %}"
                "{% endif %}"
            )
        }
    )

    assert frozenset({"IS_WORKER", "PRIOR_RESULT_STATUS"}) in groups


def test_branch_scenarios_cover_nested_combinations_without_forging_capabilities() -> None:
    scenarios = _branch_scenarios(
        {
            "HAS_MCP_WRITE",
            "ISSUES",
            "ISSUES_PATH",
            "IS_WORKER",
            "PRIOR_RESULT_STATUS",
            "SKILLS_INLINE_CONTENT",
            "shipped_skills_mode",
            "show_plan_edit_guidance",
        }
    )
    by_name = dict(scenarios)

    assert all("HAS_MCP_WRITE" not in overrides for overrides in by_name.values())
    assert by_name["baseline"] == {
        "ISSUES": "",
        "ISSUES_PATH": "",
        "IS_WORKER": "",
        "PRIOR_RESULT_STATUS": "",
        "SKILLS_INLINE_CONTENT": "",
        "shipped_skills_mode": "",
        "show_plan_edit_guidance": "",
    }
    assert by_name["SKILLS_INLINE_CONTENT=on"]["SKILLS_INLINE_CONTENT"]
    assert by_name["shipped_skills_mode=planning"]["shipped_skills_mode"] == "planning"
    assert by_name["shipped_skills_mode=development"]["shipped_skills_mode"] == "development"
    assert by_name["show_plan_edit_guidance=on"]["show_plan_edit_guidance"] == "true"
    worker_prior = by_name["IS_WORKER=on+PRIOR_RESULT_STATUS=on"]
    assert worker_prior["IS_WORKER"] == "true"
    assert worker_prior["PRIOR_RESULT_STATUS"] == "partial"
    assert all(
        not (overrides["ISSUES"] and overrides["ISSUES_PATH"]) for overrides in by_name.values()
    )


def test_branch_scenarios_cross_nested_paths_with_independent_call_gates() -> None:
    scenarios = dict(
        _branch_scenarios(
            {"IS_WORKER", "LAST_RETRY_ERROR", "PRIOR_RESULT_STATUS"},
            condition_groups=(
                frozenset({"IS_WORKER", "PRIOR_RESULT_STATUS"}),
                frozenset({"LAST_RETRY_ERROR"}),
            ),
        )
    )

    combined = scenarios["IS_WORKER=on+LAST_RETRY_ERROR=on+PRIOR_RESULT_STATUS=on"]
    assert combined == {
        "IS_WORKER": "true",
        "LAST_RETRY_ERROR": "Previous submission failed validation.",
        "PRIOR_RESULT_STATUS": "partial",
    }


def test_every_shared_partial_has_an_independent_render_target() -> None:
    context = TemplateContext.default()
    targets = {target.name: target for target in _render_targets(context)}
    expected = {name.rsplit(".", 1)[0] for name in context.partials if name.startswith("shared/")}

    assert expected <= set(targets)
    assert "render_artifact_submission(" in targets["shared/_artifact_submission"].source
    assert "render_payload_section(" in targets["shared/_payload_section"].source
    assert (
        "render_optional_artifact_skill_pointer("
        in targets["shared/_optional_artifact_skill_pointer"].source
    )


def test_audit_module_exposes_main_entry_point() -> None:
    """Audit must be runnable as ``python -m ralph.testing.audit_template_render_integrity``."""
    assert hasattr(audit_module, "main")


def test_main_exit_codes_follow_collect_violations(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main()`` must exit 0 on a clean run and 1 with the violation list
    printed when violations exist (the contract ``make verify`` relies on)."""
    monkeypatch.setattr(audit_module, "collect_violations", lambda: [])
    assert audit_main([]) == 0
    monkeypatch.setattr(
        audit_module,
        "collect_violations",
        lambda: ["x.jinja [baseline]: duplicated heading (x2): '## Steps'"],
    )
    assert audit_main([]) == 1
    captured = capsys.readouterr()
    assert "duplicated heading" in captured.out
    assert "x.jinja" in captured.out
    assert captured.out.count("Every packaged prompt template must render") == 1


# NOTE: ``test_audit_clean_on_current_templates`` was deleted in the
# wt-05-test-opti pass. That audit is already invoked as a dedicated
# ``_VERIFY_STEPS`` entry inside ``make verify`` (via
# ``python -m ralph.testing.audit_template_render_integrity``), so
# the same clean-template render check runs in the same gate. The
# pytest version added ~1 s of template-rendering cost to the
# default profile while proving nothing the verify step does not
# already prove. The remaining tests in this file cover every
# audit branch on synthetic inputs without touching the packaged
# templates, preserving the audit's behavior contract.
