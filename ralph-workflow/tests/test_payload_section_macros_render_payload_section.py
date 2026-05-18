"""Tests for shared/_payload_section.jinja macros."""

from __future__ import annotations

from pathlib import Path

from ralph.prompts.template_engine import render_template

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


class TestRenderPayloadSection:
    def test_inlines_content_when_path_absent(self) -> None:
        tpl = (
            "{{ render_payload_section('LATEST ARTIFACT', LATEST_ARTIFACT, LATEST_ARTIFACT_PATH) }}"
        )
        result = _render(
            tpl,
            {"LATEST_ARTIFACT": "artifact body", "LATEST_ARTIFACT_PATH": ""},
        )
        assert "artifact body" in result

    def test_uses_file_reference_when_path_present(self) -> None:
        tpl = (
            "{{ render_payload_section('LATEST ARTIFACT', LATEST_ARTIFACT, LATEST_ARTIFACT_PATH) }}"
        )
        result = _render(
            tpl,
            {
                "LATEST_ARTIFACT": "artifact body",
                "LATEST_ARTIFACT_PATH": ".agent/ARTIFACT.md",
            },
        )
        assert "`.agent/ARTIFACT.md`" in result
        assert "artifact body" not in result
