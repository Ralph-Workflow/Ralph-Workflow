"""Tests for shared/_payload_section.jinja macros."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.prompts.template_engine import TemplateRenderingError, render_template

_PARTIAL_NAME = "shared/_payload_section"
_IMPORT_MACROS = (
    "{% from 'shared/_payload_section.j2' import render_payload_section, render_payload_path %}"
)


def _payload_section_partial() -> dict[str, str]:
    jinja_path = (
        Path(__file__).parent.parent
        / "ralph"
        / "prompts"
        / "templates"
        / "shared"
        / "_payload_section.jinja"
    )
    return {_PARTIAL_NAME: jinja_path.read_text(encoding="utf-8")}


def _render(macro_call: str, variables: dict[str, str]) -> str:
    template = _IMPORT_MACROS + macro_call
    return render_template(template, variables, _payload_section_partial())


class TestRenderPayloadPath:
    def test_emits_file_path_reference(self) -> None:
        result = _render(
            "{{ render_payload_path('PLAN', PLAN_PATH) }}",
            {"PLAN_PATH": ".agent/PLAN.md"},
        )
        assert "`.agent/PLAN.md`" in result
        assert "PLAN:" in result

    def test_path_reference_includes_read_instruction(self) -> None:
        result = _render(
            "{{ render_payload_path('PROMPT', PROMPT_PATH) }}",
            {"PROMPT_PATH": ".agent/PROMPT.md"},
        )
        assert "Read the complete prompt" in result

    def test_raises_when_path_is_empty_string(self) -> None:
        with pytest.raises(TemplateRenderingError, match="path must not be empty"):
            _render(
                "{{ render_payload_path('PLAN', PLAN_PATH) }}",
                {"PLAN_PATH": ""},
            )

    def test_never_inlines_content(self) -> None:
        result = _render(
            "{{ render_payload_path('PLAN', PLAN_PATH) }}",
            {"PLAN_PATH": ".agent/PLAN.md"},
        )
        assert "x" * 200_000 not in result
        assert "`.agent/PLAN.md`" in result


class TestRenderPayloadSection:
    def test_inlines_content_when_path_absent(self) -> None:
        result = _render(
            "{{ render_payload_section('LATEST ARTIFACT', LATEST_ARTIFACT, LATEST_ARTIFACT_PATH) }}",  # noqa: E501
            {"LATEST_ARTIFACT": "artifact body", "LATEST_ARTIFACT_PATH": ""},
        )
        assert "artifact body" in result

    def test_uses_file_reference_when_path_present(self) -> None:
        result = _render(
            "{{ render_payload_section('LATEST ARTIFACT', LATEST_ARTIFACT, LATEST_ARTIFACT_PATH) }}",  # noqa: E501
            {
                "LATEST_ARTIFACT": "artifact body",
                "LATEST_ARTIFACT_PATH": ".agent/ARTIFACT.md",
            },
        )
        assert "`.agent/ARTIFACT.md`" in result
        assert "artifact body" not in result
