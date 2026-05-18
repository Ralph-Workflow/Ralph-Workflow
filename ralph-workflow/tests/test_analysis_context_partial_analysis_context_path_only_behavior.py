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
