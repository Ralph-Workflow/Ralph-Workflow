"""Rendered prompt contracts for visual (UI/UX) plan items.

The shipped prompt surface must not be silent about visual work:
the multimodal sidecar carries media *into* a phase with nothing
carrying captures *out*. This suite pins the contract:

* The developer iteration prompt — and its continuation twin — must
  name the retained pre-change capture set, the post-change capture
  step, the comparative design verdict, and the closed visual
  vocabulary.
* The shipped prompt surface must not invite the agent to substitute
  source-reading or appearance-assertion shortcuts for capture-based
  review.
* A resumed UI run that lacks the retained baseline must be steered
  to report the visual lane blocked rather than relabel post-change
  pixels as `before`.

Tests render the templates through the maintained Jinja surface and
assert on the rendered strings. No real subprocess, no real wire
ledger, no ``time.sleep`` — the suite stays inside the 60s combined
budget and uses no ``subprocess_e2e`` marker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.prompts.developer import (
    DeveloperPromptInputs,
    prompt_developer_iteration_xml_with_context,
)
from ralph.prompts.template_context import TemplateContext
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

# ---------------------------------------------------------------------------
# Shared rendered prompts
# ---------------------------------------------------------------------------


def _render_developer_iteration(tmp_path: Path) -> str:
    """Render the developer iteration prompt with the canonical defaults."""
    workspace = MemoryWorkspace(root=str(tmp_path))
    return prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement it", plan_content="### [S-1] Change it"
        ),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
    )


def _render_developer_continuation(tmp_path: Path) -> str:
    """Render the developer continuation prompt with the canonical defaults."""
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


# ---------------------------------------------------------------------------
# Visual guidance must appear in the developer iteration prompt
# ---------------------------------------------------------------------------


def test_developer_iteration_prompt_carries_visual_review_section() -> None:
    """The developer prompt must contain the 'Visual review' guidance section."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "Visual review" in rendered


def test_developer_iteration_prompt_names_retained_pre_change_baseline() -> None:
    """The prompt must direct the agent to trust the retained pre-change baseline."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "retained pre-change baseline" in rendered


def test_developer_iteration_prompt_directs_post_change_capture() -> None:
    """The prompt must direct the agent to capture the post-change matrix fresh."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "media.capture" in rendered
    assert "post-change" in rendered


def test_developer_iteration_prompt_enforces_three_input_verdict_contract() -> None:
    """The prompt must explain that the verdict takes only three inputs."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "before" in rendered
    assert "after" in rendered
    assert "intent" in rendered


def test_developer_iteration_prompt_forbids_source_reading_substitute() -> None:
    """The prompt must forbid substituting diff/DOM/stylesheet for captures."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "Diff, DOM, stylesheet" in rendered
    assert "NOT verdict inputs" in rendered


def test_developer_iteration_prompt_requires_capture_id_citations() -> None:
    """Every finding must cite a capture_id and region — the prompt enforces it."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "capture_id" in rendered
    assert "region" in rendered


def test_developer_iteration_prompt_lists_closed_visual_dimensions() -> None:
    """The closed dimension vocabulary must be spelled out for the agent."""
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
    """The closed severity vocabulary must be spelled out for the agent."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "blocker" in rendered
    assert "major" in rendered
    assert "minor" in rendered
    assert "nit" in rendered


def test_developer_iteration_prompt_references_design_verdict_artifact() -> None:
    """UI plan items must submit a design_verdict artifact for proof."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "design_verdict" in rendered


def test_developer_iteration_prompt_prohibits_appearance_assertions() -> None:
    """The prompt must forbid using appearance assertions as design proof."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "appearance" in rendered.lower()
    assert "padding" in rendered


def test_developer_iteration_prompt_directs_missing_baseline_to_block() -> None:
    """A resumed UI run lacking the retained baseline must report blocked."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    assert "blocked" in rendered.lower()
    assert "retained baseline" in rendered


# ---------------------------------------------------------------------------
# Visual guidance must appear in the continuation prompt too
# ---------------------------------------------------------------------------


def test_developer_continuation_prompt_carries_visual_review_section() -> None:
    """The continuation prompt must also carry the visual-review guidance."""
    rendered = _render_developer_continuation(tmp_path=Path("/tmp"))
    assert "Visual review" in rendered


def test_developer_continuation_prompt_directs_post_change_capture() -> None:
    """The continuation prompt must direct the agent to capture post-change."""
    rendered = _render_developer_continuation(tmp_path=Path("/tmp"))
    assert "media.capture" in rendered


def test_developer_continuation_prompt_enforces_three_input_verdict_contract() -> None:
    """The continuation prompt must enforce the before/after/intent triplet."""
    rendered = _render_developer_continuation(tmp_path=Path("/tmp"))
    assert "before" in rendered
    assert "after" in rendered
    assert "intent" in rendered


# ---------------------------------------------------------------------------
# Negative cases: the prompt must NOT invite shortcuts
# ---------------------------------------------------------------------------


def test_developer_iteration_prompt_does_not_recommend_asserting_padding() -> None:
    """The prompt must not present appearance assertions as a shortcut."""
    rendered = _render_developer_iteration(tmp_path=Path("/tmp"))
    # The prompt mentions `padding == "16px"` only as a NEGATIVE example,
    # never as a recommended pattern.
    if 'padding == "16px"' in rendered:
        # If the snippet appears, it must be wrapped in a sentence
        # explaining why it is wrong — never as a positive example.
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
    """Both developer-iteration templates must carry visual guidance."""
    workspace = MemoryWorkspace(root=str(tmp_path))
    rendered = prompt_developer_iteration_xml_with_context(
        context=TemplateContext.default(),
        inputs=DeveloperPromptInputs(
            prompt_content="Implement it", plan_content="### [S-1] Change it"
        ),
        workspace=workspace,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        template_name=template_name,
    )
    assert "Visual review" in rendered
    assert "retained pre-change baseline" in rendered
    assert "media.capture" in rendered
    assert "design_verdict" in rendered
