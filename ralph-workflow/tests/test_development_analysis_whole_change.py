"""Tests: development_analysis.jinja reviews the change as a whole, not only per item.

U-2 locks the whole-change review contract in the development-analysis prompt
template. The template must:

- Add a ``### Whole-change review`` section that names the four integration
  findings: parallel pieces fit together, nothing outside the plan regressed,
  no unrelated scope, and repository policy compliance.
- Use ``DA-###`` items for whole-change findings (no new section).
- Replace the ``every required plan reference`` phrasing with
  ``whatever references the plan actually uses`` so a prose plan is judged
  on request coverage, not on numeric ID matching.
- Treat the whole-change review as required, not optional.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.template_engine import render_template
from ralph.prompts.types import SessionCapabilities, capability_template_variables


@cache
def _render_development_analysis() -> str:
    context = TemplateContext.default()
    session = SessionCapabilities.defaults_for_drain(SessionDrain.ANALYSIS)
    return render_template(
        context.registry.get_template("development_analysis"),
        {
            **capability_template_variables(session.capabilities, session.policy_flags),
            "PRODUCT_CRITERIA_PATH": "fixtures/request.md",
            "HAS_DOCS_MCP": "",
            "DOCS_MCP_PORT": "localhost:6280",
            "DOCS_LOOKUP_PHASE": "analysis",
            "DOCS_LOOKUP_VARIANT": "",
            "PLAN_PATH": "fixtures/plan.md",
            "LAST_RETRY_ERROR": "",
            "gate_script_policy_path": "docs/gates.md",
            "approved_tools": "python",
            "submit_tool_names": "ralph_submit_md_artifact",
            "verify_tool_names": "ralph_verify_md_artifact",
            "declare_complete_tool_names": "declare_complete",
            "artifact_type": "development_analysis_decision",
        },
        context.partials,
    )


@cache
def _development_analysis_source() -> str:
    return TemplateContext.default().registry.get_template("development_analysis")


def _format_doc_path(*parts: str) -> Path:
    return Path(__file__).parent.parent.joinpath("ralph", "mcp", "artifacts", *parts)


class TestDevelopmentAnalysisWholeChange:
    """U-2: whole-change review is required and structured; format doc and example match."""

    def test_whole_change_review_section_is_present_in_rendered_template(self) -> None:
        rendered = _render_development_analysis()
        assert "### Whole-change review" in rendered

    def test_four_what_to_look_for_items_are_present(self) -> None:
        rendered = _render_development_analysis()
        for item in (
            "Parallel pieces fit together at the integration seam",
            "Nothing outside the plan regressed",
            "No unrelated scope",
            "Repository policy compliance",
        ):
            assert item in rendered, item

    def test_repository_policy_compliance_names_agents_and_policy_dir(self) -> None:
        rendered = _render_development_analysis()
        assert "AGENTS.md" in rendered
        assert "docs/ralph-workflow-policy/" in rendered

    def test_outside_regression_requires_fresh_worktree_evidence(self) -> None:
        rendered = _render_development_analysis()
        # "Nothing outside the plan regressed" must be backed by a fresh probe,
        # not an implementer narrative.
        assert "fast path" in rendered.lower() or "full gate" in rendered.lower()
        assert "implementer summary" in rendered.lower()

    def test_unrelated_scope_cites_drift_categories(self) -> None:
        rendered = _render_development_analysis()
        assert "unrequested refactors" in rendered
        assert "drive-by cleanups" in rendered

    def test_da_items_reused_for_whole_change_findings(self) -> None:
        """Integration findings reuse ``DA-###`` items; no new section."""
        source = _development_analysis_source()
        assert "DA-###" in source
        # The whole-change review paragraph must point at the same ID shape.
        assert "DA-###" in source
        # No separate integration section is introduced.
        assert "## Integration" not in source
        assert "### Integration" not in source
        # The template explicitly says there is no separate integration section.
        rendered = _render_development_analysis()
        assert "no separate integration section" in rendered

    def test_whole_change_review_is_required_not_optional(self) -> None:
        rendered = _render_development_analysis()
        # The template must say whole-change findings are required, not optional.
        assert "required, not optional" in rendered

    def test_whatever_references_the_plan_actually_uses_phrasing_present(self) -> None:
        source = _development_analysis_source()
        assert "whatever references the plan actually uses" in source

    def test_old_every_required_plan_reference_phrasing_removed(self) -> None:
        """The old 'every required plan reference' phrasing must not appear in
        development_analysis.jinja anymore. The shared ``_developer_iteration_guidance.j2``
        partial still uses it, but U-2 only changes the development-analysis
        template and its format doc.
        """
        source = _development_analysis_source()
        assert "every required plan reference" not in source

    def test_prose_plan_without_ids_is_judged_on_request_coverage(self) -> None:
        """A prose plan is judged on whether its requests are covered, not
        whether a numeric identifier matches.
        """
        source = _development_analysis_source()
        assert "prose plan without IDs" in source or "prose plan" in source
        # The lock here is that the template now explains the rule rather than
        # silently requiring numeric IDs.
        assert "numeric identifier matches" in source

    def test_whole_change_section_appears_between_fanout_and_criteria(self) -> None:
        """The whole-change review lives between the verification fan-out and
        the criteria-and-verdicts block. Locking the position prevents a future
        edit from drifting the section into a place where it no longer frames
        the per-item review.
        """
        rendered = _render_development_analysis()
        fanout = rendered.index("### Verification fan-out")
        whole_change = rendered.index("### Whole-change review")
        criteria = rendered.index("## Criteria and verdicts")
        decision = rendered.index("## Decision artifact")
        assert fanout < whole_change < criteria < decision

    def test_format_doc_documents_whole_change_findings(self) -> None:
        text = _format_doc_path("format_docs", "development_analysis_decision.md").read_text(encoding="utf-8")
        assert "Whole-change findings" in text or "whole-change" in text.lower()
        assert "AGENTS.md" in text
        assert "docs/ralph-workflow-policy/" in text
        # Plan reference coverage rule.
        assert "whatever references the plan actually uses" in text

    def test_example_demonstrates_da_item_for_whole_change_finding(self) -> None:
        text = _format_doc_path("format_docs", "examples", "development_analysis_decision.md").read_text(encoding="utf-8")
        # The example must include a second DA-### item to show the
        # whole-change item shape is identical to the criterion item shape.
        assert "[DA-001]" in text
        assert "[DA-002]" in text
        # The whole-change example item must read as a parallel-piece finding.
        assert "parallel" in text.lower() or "integration" in text.lower()

    def test_embedded_example_names_concrete_remaining_work(self) -> None:
        """DA-015 / DA-016: the embedded example names concrete leftover work.

        The format doc rule (development_analysis_decision.md) requires
        every ``## What Came Up Short`` finding to carry a non-empty
        ``Remaining work:`` statement naming concrete leftover
        development work. The embedded example in
        ``development_analysis.jinja`` must model that rule rather than
        defer the choice back to the developer with a vague sentence
        like "developer decides how to close this finding in the next
        iteration".
        """
        source = _development_analysis_source()
        # The vague deferred-choice phrase is what DA-015 / DA-016
        # explicitly flagged: the example identified no outstanding
        # development work.
        assert "developer decides how to close this finding in the next iteration" not in source
        # The example must still carry a Remaining work statement.
        assert "Remaining work:" in source
        # And it must name a concrete action the developer can perform
        # (a specific test file plus a behavior the next change
        # exercises).
        assert "tests/test_foo.py" in source

    def test_format_doc_request_changes_example_names_concrete_remaining_work(self) -> None:
        """The format doc's own request-changes example mirrors the rule."""
        text = _format_doc_path("format_docs", "development_analysis_decision.md").read_text(encoding="utf-8")
        assert "developer decides how to close this finding in the next iteration" not in text
        assert "Remaining work:" in text
        assert "tests/test_foo.py" in text

    def test_format_doc_rule_requires_concrete_remaining_work(self) -> None:
        """The Sections rule must continue to require concrete leftover
        development work so the rule and the example agree. Locking the
        rule prevents a regression where the rule drifts to a vague
        "developer decides" placeholder and the example then mirrors it.
        """
        text = " ".join(
            _format_doc_path("format_docs", "development_analysis_decision.md")
            .read_text(encoding="utf-8")
            .split()
        )
        assert "naming concrete leftover development work" in text
        assert "`Remaining work:`" in text
