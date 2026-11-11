from __future__ import annotations

from pathlib import Path

from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities
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
    def _analysis_templates(self) -> list[Path]:
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
                "render_payload_path('PROMPT', PRODUCT_CRITERIA_PATH)" in source
                or 'render_payload_path("PROMPT", PRODUCT_CRITERIA_PATH)' in source
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
