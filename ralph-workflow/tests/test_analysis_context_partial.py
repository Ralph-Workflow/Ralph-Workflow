from __future__ import annotations

from pathlib import Path

from ralph.prompts.template_engine import render_template

_TEMPLATES_DIR = Path(__file__).parent.parent / "ralph" / "prompts" / "templates"

_PAYLOAD_SECTION_NAME = "shared/_payload_section"
_ANALYSIS_CONTEXT_NAME = "shared/_analysis_context"

_INCLUDE_TEMPLATE = (
    "{% from 'shared/_payload_section.j2' import render_payload_section %}"
    "{% include 'shared/_analysis_context.j2' %}"
)


def _load_partials() -> dict[str, str]:
    return {
        _PAYLOAD_SECTION_NAME: (_TEMPLATES_DIR / "shared" / "_payload_section.jinja").read_text(
            encoding="utf-8"
        ),
        _ANALYSIS_CONTEXT_NAME: (_TEMPLATES_DIR / "shared" / "_analysis_context.jinja").read_text(
            encoding="utf-8"
        ),
    }


def _render(variables: dict[str, str]) -> str:
    return render_template(_INCLUDE_TEMPLATE, variables, _load_partials())


class TestAnalysisContextSuppression:
    def test_suppressed_when_both_issues_and_feedback_are_empty_strings(self) -> None:
        result = _render(
            {
                "ISSUES": "",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "ANALYSIS CONTEXT" not in result

    def test_suppressed_when_no_analysis_variables_are_defined(self) -> None:
        result = _render({})

        assert "ANALYSIS CONTEXT" not in result

    def test_issues_section_absent_when_issues_content_and_path_both_empty(self) -> None:
        result = _render(
            {
                "ISSUES": "",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "feedback body",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "ISSUES:" not in result

    def test_feedback_section_absent_when_feedback_content_and_path_both_empty(self) -> None:
        result = _render(
            {
                "ISSUES": "issue body",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "ANALYSIS FEEDBACK" not in result


class TestAnalysisContextRendering:
    def test_renders_issues_content_when_issues_present(self) -> None:
        result = _render(
            {
                "ISSUES": "issue body",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "issue body" in result

    def test_renders_feedback_content_when_analysis_feedback_present(self) -> None:
        result = _render(
            {
                "ISSUES": "",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "feedback body",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "feedback body" in result

    def test_renders_both_blocks_when_both_present(self) -> None:
        result = _render(
            {
                "ISSUES": "issue body",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "feedback body",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "issue body" in result
        assert "feedback body" in result

    def test_header_present_when_issues_has_content(self) -> None:
        result = _render(
            {
                "ISSUES": "something",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "ANALYSIS CONTEXT" in result

    def test_header_present_when_feedback_has_content(self) -> None:
        result = _render(
            {
                "ISSUES": "",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "something",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "ANALYSIS CONTEXT" in result


class TestAnalysisContextPathOnlyBehavior:
    def test_renders_when_only_issues_path_is_set(self) -> None:
        result = _render(
            {
                "ISSUES": "",
                "ISSUES_PATH": ".agent/ISSUES.md",
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "ANALYSIS CONTEXT" in result
        assert "`.agent/ISSUES.md`" in result

    def test_renders_when_only_feedback_path_is_set(self) -> None:
        result = _render(
            {
                "ISSUES": "",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": ".agent/REVIEW_ANALYSIS_DECISION.md",
            }
        )

        assert "ANALYSIS CONTEXT" in result
        assert "`.agent/REVIEW_ANALYSIS_DECISION.md`" in result


class TestAnalysisContextPathBehavior:
    def test_uses_file_reference_for_issues_when_path_set(self) -> None:
        result = _render(
            {
                "ISSUES": "issue body",
                "ISSUES_PATH": ".agent/ISSUES.md",
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "`.agent/ISSUES.md`" in result
        assert "issue body" not in result

    def test_uses_file_reference_for_feedback_when_path_set(self) -> None:
        result = _render(
            {
                "ISSUES": "",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "feedback body",
                "ANALYSIS_FEEDBACK_PATH": ".agent/REVIEW_ANALYSIS_DECISION.md",
            }
        )

        assert "`.agent/REVIEW_ANALYSIS_DECISION.md`" in result
        assert "feedback body" not in result

    def test_inlines_issues_content_when_no_path(self) -> None:
        result = _render(
            {
                "ISSUES": "inline issue content",
                "ISSUES_PATH": "",
                "ANALYSIS_FEEDBACK": "",
                "ANALYSIS_FEEDBACK_PATH": "",
            }
        )

        assert "inline issue content" in result
