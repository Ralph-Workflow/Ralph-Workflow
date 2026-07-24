"""Tests: analysis templates never inline PROMPT or PLAN regardless of content size."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import ralph.prompts.materialize as materialize_module
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

_TINY_PROMPT = "Implement the feature."
_LARGE_CONTENT = "X" * (100 * 1024 + 1)

_MINIMAL_DEV_RESULT = (
    "---\n"
    "type: development_result\n"
    "status: completed\n"
    "---\n\n"
    "## Summary\n\n- [SUM-1] Done.\n\n"
    "## Files Changed\n\n- [F-1] src/app.py\n"
)


_TEMPLATES_DIR = Path(__file__).parent.parent / "ralph" / "prompts" / "templates"
_MIN_EXPECTED_ANALYSIS_TEMPLATES = 2

#: Templates matching ``*_analysis.jinja`` that are NOT in-graph analysis phases
#: and therefore receive no PROMPT and no PLAN payload. See
#: ``_analysis_templates`` for the full rationale, and
#: ``test_out_of_graph_policy_template_has_no_payloads`` for the compensating
#: control that keeps this exclusion honest.
_OUT_OF_GRAPH_ANALYSIS_TEMPLATES: frozenset[str] = frozenset({"policy_remediation_analysis.jinja"})


def _write_plan_handoff(workspace: MemoryWorkspace) -> None:
    workspace.write(
        ".agent/PLAN.md",
        "# Execution Plan\n\n"
        "1. Add the missing regression test.\n"
        "2. Tighten prompt preconditions.\n",
    )


def _render_development_analysis(
    tmp_path: Path,
    *,
    prompt_content: str = _TINY_PROMPT,
) -> str:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", prompt_content)
    _write_plan_handoff(workspace)
    workspace.write(".agent/artifacts/development_result.md", _MINIMAL_DEV_RESULT)
    with patch.object(materialize_module, "_git_diff", return_value="diff"):
        path = materialize_prompt_for_phase(
            PromptPhaseContext(
                phase="development_analysis",
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(
                artifacts_policy=policy.artifacts,
            ),
        )
    return workspace.read(path)


class TestAnalysisTemplatesStructuralInvariants:
    """Verify analysis template source never uses render_payload_section for PROMPT or PLAN.

    These tests read the raw .jinja source files and assert structural invariants that
    protect against regression — even if the rendered output happens to look correct.
    """

    def _analysis_templates(self) -> list[Path]:
        """The IN-GRAPH analysis templates the payload-path invariant governs.

        The invariant below exists because an in-graph analysis phase is handed
        the user's PROMPT and the execution PLAN, both of which can be arbitrarily
        large; inlining them into the rendered prompt blows the context window, so
        they must be passed BY PATH.

        ``policy_remediation_analysis.jinja`` is excluded because it is not an
        in-graph analysis phase and has neither payload to pass. It runs
        out-of-graph in the startup policy preflight, reviewing the project's
        policy documents; it never receives the user's PROMPT, and its drain is
        explicitly DENIED ``artifact.plan_read`` in ``ralph/mcp/session_plan.py``,
        so there is no PLAN for it to render by any means. The exclusion is
        compensated by ``test_out_of_graph_policy_template_has_no_payloads``
        below, which proves it does not inline what it is not given.
        """
        return sorted(
            path
            for path in _TEMPLATES_DIR.glob("*_analysis.jinja")
            if path.name not in _OUT_OF_GRAPH_ANALYSIS_TEMPLATES
        )

    def test_at_least_two_analysis_templates_exist(self) -> None:
        templates = self._analysis_templates()
        count = len(templates)
        assert count >= _MIN_EXPECTED_ANALYSIS_TEMPLATES, (
            f"Expected >={_MIN_EXPECTED_ANALYSIS_TEMPLATES} *_analysis.jinja templates,"
            f" found: {templates}"
        )

    def test_out_of_graph_policy_template_has_no_payloads(self) -> None:
        """The excluded template must genuinely have no PROMPT/PLAN payload.

        This is the compensating control for the exclusion above: if the policy
        analysis template ever starts consuming a payload, it must come back under
        the payload-path invariant rather than silently inlining it.
        """
        for name in _OUT_OF_GRAPH_ANALYSIS_TEMPLATES:
            source = (_TEMPLATES_DIR / name).read_text(encoding="utf-8")
            assert "render_payload_section" not in source, f"{name}: must never inline a payload"
            assert "render_payload_path" not in source, (
                f"{name}: is out-of-graph and receives no PROMPT/PLAN payload; "
                "if that changed, remove it from _OUT_OF_GRAPH_ANALYSIS_TEMPLATES"
            )

    def test_prompt_uses_render_payload_path_not_section(self) -> None:
        for template in self._analysis_templates():
            source = template.read_text(encoding="utf-8")
            uses_path = (
                "render_payload_path('PROMPT'" in source or 'render_payload_path("PROMPT"' in source
            )
            assert uses_path, f"{template.name}: PROMPT must use render_payload_path"
            assert "render_payload_section('PROMPT'" not in source, (
                f"{template.name}: render_payload_section('PROMPT' is forbidden"
            )
            assert 'render_payload_section("PROMPT"' not in source, (
                f'{template.name}: render_payload_section("PROMPT" is forbidden'
            )

    def test_plan_uses_render_payload_path_not_section(self) -> None:
        for template in self._analysis_templates():
            source = template.read_text(encoding="utf-8")
            if template.name == "planning_analysis.jinja":
                assert "render_payload_path('PLAN'" not in source
                assert 'render_payload_path("PLAN"' not in source
                assert "GET_MD_DRAFT_TOOL_REFERENCE" in source
                continue
            uses_path = (
                "render_payload_path('PLAN'" in source or 'render_payload_path("PLAN"' in source
            )
            assert uses_path, f"{template.name}: PLAN must use render_payload_path"
            assert "render_payload_section('PLAN'" not in source, (
                f"{template.name}: render_payload_section('PLAN' is forbidden"
            )
            assert 'render_payload_section("PLAN"' not in source, (
                f'{template.name}: render_payload_section("PLAN" is forbidden'
            )
