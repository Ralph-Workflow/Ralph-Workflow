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
