from __future__ import annotations

import json
from io import StringIO
from typing import TYPE_CHECKING, cast

from rich.console import Console

if TYPE_CHECKING:
    from pathlib import Path

from ralph.display.artifact_renderer import (
    render_plan_artifact,
)
from ralph.display.context import DisplayContext, make_display_context


def _make_console() -> Console:
    return Console(
        file=cast("StringIO", StringIO()),
        force_terminal=True,
        color_system=None,
        width=120,
    )


def _make_display_context() -> DisplayContext:
    console = _make_console()
    return make_display_context(console=console, env={})


def _console_output(console: Console) -> str:
    return cast("StringIO", console.file).getvalue()


class TestRenderPlanArtifact:
    def test_renders_plan_block_when_file_present(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "plan.json").write_text(
            json.dumps(
                {
                    "content": {
                        "summary": {
                            "context": "Test plan",
                            "scope_items": ["item1", "item2"],
                        },
                        "steps": [{"title": "one"}, {"title": "two"}, {"title": "three"}],
                        "risks_mitigations": ["Risk 1", "Risk 2"],
                    }
                }
            ),
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "PLAN" in output
        assert "Test plan" in output
        assert "item1" in output
        assert "item2" in output
        assert "3" in output  # 3 steps
        assert "Risk 1" in output

    def test_renders_full_markdown_handoff_when_plan_md_present(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        agent_dir.mkdir(parents=True)
        plan_markdown = (
            "# Execution Plan\n\n"
            "## Steps\n"
            "1. Add regression tests\n"
            "2. Fix pipeline routing\n\n"
            "## Work Units\n"
            "- **api** — Update API surface\n"
        )
        (agent_dir / "PLAN.md").write_text(plan_markdown, encoding="utf-8")
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "PLAN" in output
        assert "Add regression tests" in output
        assert "Fix pipeline routing" in output
        assert "api" in output

    def test_renders_fresh_plan_handoff_when_plan_json_exists(self, tmp_path: Path) -> None:
        agent_dir = tmp_path / ".agent"
        artifacts_dir = agent_dir / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (agent_dir / "PLAN.md").write_text("STALE PLAN", encoding="utf-8")
        (artifacts_dir / "plan.json").write_text(
            json.dumps(
                {
                    "type": "plan",
                    "content": {
                        "summary": {
                            "context": "Fresh plan context",
                            "scope_items": [
                                {"text": "Refresh PLAN.md before rendering"},
                                {"text": "Keep JSON authoritative"},
                                {"text": "Show the full handoff to users"},
                            ],
                        },
                        "steps": [
                            {
                                "number": 1,
                                "title": "Render the fresh handoff",
                                "content": "Do not trust stale markdown",
                            }
                        ],
                        "critical_files": {
                            "primary_files": [
                                {"path": "ralph/display/artifact_renderer.py", "action": "modify"}
                            ]
                        },
                        "risks_mitigations": [
                            {"risk": "Stale plan", "mitigation": "Regenerate from JSON"}
                        ],
                        "verification_strategy": [
                            {"method": "pytest", "expected_outcome": "fresh handoff renders"}
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "Fresh plan context" in output
        assert "STALE PLAN" not in output

    def test_emits_hint_when_file_absent(self, tmp_path: Path) -> None:
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "[plan]" in output
        assert "no plan artifact on disk" in output

    def test_emits_hint_for_malformed_json(self, tmp_path: Path) -> None:
        artifacts_dir = tmp_path / ".agent" / "artifacts"
        artifacts_dir.mkdir(parents=True)
        (artifacts_dir / "plan.json").write_text("not valid json{", encoding="utf-8")
        ctx = _make_display_context()
        render_plan_artifact(tmp_path, ctx)
        output = _console_output(ctx.console)
        assert "[plan]" in output
        assert "no plan artifact on disk" in output
