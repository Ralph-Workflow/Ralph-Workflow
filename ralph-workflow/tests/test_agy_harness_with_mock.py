"""Black-box end-to-end tests for the AGY smoke harness using the mock binary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import smoke as smoke_module
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.smoke_test_result import SmokeTestResult
from ralph.pipeline import effect_executor as effect_executor_module
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunResult,
    run_smoke_plumbing,
)
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    from collections import deque

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


def _mock_agy_path() -> Path:
    """Return the absolute path to the mock AGY shell wrapper."""
    return Path(__file__).resolve().parent / "_support" / "mock_agy.sh"


def _write_smoke_prompt(prompt_file: Path) -> None:
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(
        "Create a small JavaScript todo list at tmp/interactive-agy-smoke/todo-list.js.",
        encoding="utf-8",
    )


def _run_agy_smoke_plumbing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    behavior: str = "normal",
) -> SmokeRunResult:
    """Drive ``run_smoke_plumbing`` with the mock AGY binary in ``tmp_path``."""
    mock_path = _mock_agy_path()
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_path))
    monkeypatch.setenv("MOCK_AGY_BEHAVIOR", behavior)
    monkeypatch.setenv("MOCK_AGY_ARTIFACT_DIR", str(tmp_path))
    monkeypatch.setattr(
        smoke_module,
        "resolve_workspace_scope",
        lambda *_args, **_kwargs: WorkspaceScope(tmp_path),
    )

    workspace_scope = WorkspaceScope(tmp_path)
    config = load_config(None, {}, workspace_scope=workspace_scope)
    config = smoke_module._apply_agy_binary_override_to_config(config)
    # Dynamic agy/<model> aliases are resolved from builtins, not from
    # config.agents, so inject the overridden config under the exact
    # agent name so the mock binary is honored.
    agent_name = "agy/Claude Sonnet 4.6 (Thinking)"
    agent_config = AgentRegistry.from_config(config).get(agent_name)
    if agent_config is not None:
        agent_config = smoke_module._maybe_apply_agy_binary_override(agent_config)
        overridden_agents = dict(config.agents)
        overridden_agents[agent_name] = agent_config
        config = config.model_copy(update={"agents": overridden_agents})

    display_context = make_display_context()
    deps = DefaultPipelineFactory().build(config, display_context)

    smoke_dir = tmp_path / "tmp" / "interactive-agy-smoke"
    prompt_file = smoke_dir / "PROMPT.md"
    _write_smoke_prompt(prompt_file)

    return run_smoke_plumbing(
        config=config,
        workspace_root=tmp_path,
        agent_name="agy/Claude Sonnet 4.6 (Thinking)",
        prompt_file=prompt_file,
        output_file=smoke_dir / "todo-list.js",
        display_context=display_context,
        pipeline_deps=deps,
    )


def test_agy_harness_produces_real_output_with_mock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The full harness reports file=yes, tool activity=yes, artifact=yes, no breaks."""
    result = _run_agy_smoke_plumbing(monkeypatch, tmp_path)
    assert result.file_created is True
    assert result.session_id is not None
    assert result.explicit_completion_seen is True
    assert result.tool_activity_seen is True
    assert result.artifact_submitted is True
    assert result.parsed_event_count > 0
    text_lines = [line for line in result.meaningful_output_lines if line.startswith("text:")]
    assert text_lines, (
        f"Expected at least one text-classified line, got: {result.meaningful_output_lines}"
    )
    assert all("raw:" not in line for line in result.meaningful_output_lines), (
        f"No line should be classified as raw, got: {result.meaningful_output_lines}"
    )
    assert any(len(line) > len("text: ") for line in text_lines), (
        f"Expected at least one text-classified line with non-empty content, "
        f"got: {result.meaningful_output_lines}"
    )


def test_agy_harness_writes_artifact_with_correct_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The persisted artifact content validates against SmokeTestResult."""
    _run_agy_smoke_plumbing(monkeypatch, tmp_path)
    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.json"
    raw = json.loads(artifact_path.read_text(encoding="utf-8"))
    validated = SmokeTestResult.model_validate(raw["content"])
    assert validated.status == "passed"
    assert validated.output_file == "tmp/interactive-agy-smoke/todo-list.js"
    assert validated.observed_breaks == []
    assert "tool activity" in validated.headless_guide_checks
    assert "no output" not in (validated.observed_working or [])
    assert validated.summary


def test_agy_harness_writes_todo_list_with_expected_methods(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The todo-list.js file exports a function and contains the expected method names."""
    _run_agy_smoke_plumbing(monkeypatch, tmp_path)
    todo_path = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    text = todo_path.read_text(encoding="utf-8")
    assert "function createTodoList" in text
    assert "module.exports" in text
    for method in ("add", "list", "complete", "remove"):
        assert method in text


def test_agy_harness_quota_branch_emits_informational_not_live_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With MOCK_AGY_BEHAVIOR=quota_exhausted the harness reports the mock-empty note."""
    result = _run_agy_smoke_plumbing(monkeypatch, tmp_path, behavior="quota_exhausted")
    assert any("mock AGY produced empty stdout by design" in error for error in result.errors)
    assert not any("individual API quota exhausted" in error for error in result.errors)
    assert not any("RESOURCE_EXHAUSTED" in error for error in result.errors)


def test_agy_harness_captures_both_sinks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """``execute_agent_effect`` receives both raw and rendered output sinks."""
    captured_raw: deque[str] | None = None
    captured_rendered: deque[str] | None = None

    original_execute = effect_executor_module.execute_agent_effect

    def _wrapped_execute(*args: object, **kwargs: object) -> object:
        nonlocal captured_raw, captured_rendered
        captured_raw = kwargs.get("raw_output_sink")
        captured_rendered = kwargs.get("rendered_output_sink")
        return original_execute(*args, **kwargs)

    monkeypatch.setattr(
        "ralph.pipeline.plumbing.smoke_plumbing.execute_agent_effect",
        _wrapped_execute,
    )
    _run_agy_smoke_plumbing(monkeypatch, tmp_path)
    assert captured_raw is not None
    assert captured_rendered is not None
    assert len(captured_raw) >= 3


def test_agy_harness_session_id_present_with_mock(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The harness extracts a session id matching the AGY smoke run id pattern."""
    result = _run_agy_smoke_plumbing(monkeypatch, tmp_path)
    assert result.session_id is not None
    assert result.session_id.startswith("interactive-agy-smoke-")
