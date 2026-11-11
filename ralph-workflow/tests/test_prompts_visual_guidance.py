from __future__ import annotations

import functools
from pathlib import Path

import pytest

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace


@functools.lru_cache(maxsize=4)
def _render_developer_iteration(tmp_path: Path) -> str:
    workspace = MemoryWorkspace(root=str(tmp_path))
    return prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement it", plan_content="### [S-1] Change it"
        ),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
    )


@functools.lru_cache(maxsize=4)
def _render_developer_continuation(tmp_path: Path) -> str:
    workspace = MemoryWorkspace(root=str(tmp_path))
    return prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement it", plan_content="### [S-1] Change it"
        ),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name="developer_iteration_continuation.jinja",
    )


_WARM_ITERATION = _render_developer_iteration(Path("/tmp"))
_WARM_CONTINUATION = _render_developer_continuation(Path("/tmp"))


def test_developer_iteration_prompt_carries_visual_review_section() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "Visual review" in rendered


def test_developer_iteration_prompt_names_retained_pre_change_baseline() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "retained pre-change capture" in rendered


def test_developer_iteration_prompt_directs_post_change_capture() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "media.capture" in rendered
    assert "post-change" in rendered


def test_developer_iteration_prompt_enforces_three_input_verdict_contract() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "before" in rendered
    assert "after" in rendered
    assert "intent" in rendered


def test_developer_iteration_prompt_forbids_source_reading_substitute() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "code, DOM, CSS" in rendered
    assert "not visual-verdict evidence" in rendered


def test_developer_iteration_prompt_requires_capture_id_citations() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "capture_id" in rendered
    assert "region" in rendered


def test_developer_iteration_prompt_lists_closed_visual_dimensions() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    for dimension in (
        "hierarchy",
        "alignment",
        "spacing",
        "typography",
        "legibility",
        "density",
        "completeness",
        "clipping",
    ):
        assert dimension in rendered, f"dimension {dimension!r} not in prompt"


def test_developer_iteration_prompt_lists_closed_severities() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "blocker" in rendered
    assert "major" in rendered
    assert "minor" in rendered
    assert "nit" in rendered


def test_developer_iteration_prompt_references_design_verdict_artifact() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "design_verdict" in rendered


def test_developer_iteration_prompt_prohibits_appearance_assertions() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "appearance" in rendered.lower()
    assert "functional appearance assertions" in rendered


def test_developer_iteration_prompt_directs_missing_baseline_to_block() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "blocked" in rendered.lower()
    assert "retained baseline" in rendered


def test_developer_continuation_prompt_carries_visual_review_section() -> None:
    rendered = _render_developer_continuation(tmp_path=Path("/tmp"))
    assert "Visual review" in rendered


def test_developer_continuation_prompt_directs_post_change_capture() -> None:
    rendered = _render_developer_continuation(tmp_path=Path("/tmp"))
    assert "media.capture" in rendered


def test_developer_continuation_prompt_enforces_three_input_verdict_contract() -> None:
    rendered = _render_developer_continuation(tmp_path=Path("/tmp"))
    assert "before" in rendered
    assert "after" in rendered
    assert "intent" in rendered


def test_developer_iteration_prompt_does_not_recommend_asserting_padding() -> None:
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    if 'padding == "16px"' in rendered:
        idx = rendered.index('padding == "16px"')
        surrounding = rendered[max(0, idx - 200) : idx + 200].lower()
        assert any(
            forbidden in surrounding
            for forbidden in ("not", "wrong", "forbidden", "is a functional", "no ")
        ), (
            "padding == '16px' appears in the prompt without an "
            "explicit negation context — the prompt must frame it as "
            "a negative example, never as a recommended pattern"
        )


@pytest.mark.parametrize(
    "template_name",
    ["developer_iteration.jinja", "developer_iteration_continuation.jinja"],
)
def test_both_iteration_templates_carry_visual_guidance(
    tmp_path: Path,
    template_name: str,
) -> None:
    if template_name == "developer_iteration.jinja":
        rendered = _render_developer_iteration(tmp_path)
    else:
        rendered = _render_developer_continuation(tmp_path)
    assert "Visual review" in rendered
    assert "retained pre-change capture" in rendered
    assert "media.capture" in rendered
    assert "design_verdict" in rendered
