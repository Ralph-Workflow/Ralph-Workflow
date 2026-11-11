from __future__ import annotations

import os
import unittest.mock
from collections.abc import Callable
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from ralph.agents.completion_signals import _check_completion_sentinel
from ralph.agents.display_capabilities import DisplayCapability
from ralph.agents.parsers.agy import AgyParser
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import smoke as smoke_module
from ralph.cli.commands.smoke_binary_override import (
    apply_smoke_binary_override,
    apply_smoke_binary_overrides_to_config,
)
from ralph.config.loader import load_config
from ralph.display.context import make_display_context
from ralph.mcp.artifacts.completion_receipts import artifact_receipt_present
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.smoke_test_result import SmokeTestResult
from ralph.mcp.server._wire_ledger import wire_evidence_for
from ralph.pipeline.factory import DefaultPipelineFactory
from ralph.pipeline.plumbing.smoke_plumbing import (
    SmokeRunResult,
    resolve_smoke_harness_spec,
    run_smoke_plumbing,
)
from ralph.workspace.scope import WorkspaceScope

import_module("ralph.mcp.artifacts.markdown.specs")

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(20)]


def _mock_agy_path() -> Path:
    return Path(__file__).resolve().parent / "_support" / "mock_agy.sh"


def _write_smoke_prompt(prompt_file: Path) -> None:
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text(
        "Create a small JavaScript todo list at tmp/interactive-agy-smoke/todo-list.js.",
        encoding="utf-8",
    )


def _run_agy_smoke_plumbing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    behavior: str = "normal",
    agent_name: str = "agy/gemini-3.6-flash-low",
    subagents: bool = False,
) -> SmokeRunResult:
    mock_path = _mock_agy_path()
    monkeypatch.setenv("RALPH_AGY_BINARY", str(mock_path))
    monkeypatch.setenv("MOCK_AGY_BEHAVIOR", behavior)
    monkeypatch.setenv("MOCK_AGY_ARTIFACT_DIR", str(tmp_path))
    if subagents:
        monkeypatch.setenv("MOCK_AGY_SUBAGENT", "1")

    def resolve_scope(*_args: object, **_kwargs: object) -> WorkspaceScope:
        return WorkspaceScope(tmp_path)

    monkeypatch.setattr(smoke_module, "resolve_workspace_scope", resolve_scope)

    workspace_scope = WorkspaceScope(tmp_path)
    config = load_config(None, {}, workspace_scope=workspace_scope)
    config = apply_smoke_binary_overrides_to_config(config)
    agent_config = AgentRegistry.from_config(config).get(agent_name)
    if agent_config is not None:
        agent_config = apply_smoke_binary_override(agent_config)
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
        agent_name=agent_name,
        prompt_file=prompt_file,
        output_file=smoke_dir / "todo-list.js",
        display_context=display_context,
        pipeline_deps=deps,
        subagents=subagents,
    )


_smoke_result_cache: dict[tuple[str, str], tuple[SmokeRunResult, Path, object]] = {}


@pytest.fixture(scope="module")
def cached_default_smoke(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[SmokeRunResult, Path]:
    key = ("normal", "agy/claude-sonnet-5-5-high")
    cached = _smoke_result_cache.get(key)
    if cached is not None:
        return cached[0], cached[1]

    workspace = tmp_path_factory.mktemp("agy_default_smoke_workspace")
    monkeypatch = pytest.MonkeyPatch()
    try:
        result = _run_agy_smoke_plumbing(
            workspace,
            monkeypatch,
            behavior="normal",
            agent_name="agy/claude-sonnet-5-5-high",
        )
        deps = None
        _smoke_result_cache[key] = (result, workspace, deps)
        return result, workspace
    finally:
        monkeypatch.undo()


def test_agy_harness_produces_real_output_with_mock(
    cached_default_smoke: tuple[SmokeRunResult, Path],
) -> None:
    result, _workspace = cached_default_smoke
    assert result.file_created is True
    assert result.session_id is not None
    assert result.explicit_completion_seen.holds is True
    assert result.tool_activity_seen.holds is True
    assert any("tool_use: createTodoList" in line for line in result.meaningful_output_lines)
    assert any(line.startswith("tool_result:") for line in result.meaningful_output_lines)
    assert result.artifact_submitted.holds is True
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


def test_agy_harness_subagent_stream_json_is_observed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _run_agy_smoke_plumbing(tmp_path, monkeypatch, subagents=True)

    assert result.subagent_dispatch_count == 2
    assert result.subagent_dispatch_seen is True
    assert result.subagent_result_seen is True
    assert result.post_subagent_activity_seen is True
    assert "subagent dispatch was not observed" not in result.errors
    assert "not every subagent dispatch has a correlated result" not in result.errors


def test_agy_harness_writes_artifact_with_correct_schema(
    cached_default_smoke: tuple[SmokeRunResult, Path],
) -> None:
    _result, workspace = cached_default_smoke
    artifact_path = workspace / ".agent" / "artifacts" / "smoke_test_result.md"
    markdown = artifact_path.read_text(encoding="utf-8")
    content, diagnostics = parse_and_validate(markdown, get_spec("smoke_test_result"))
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert errors == [], f"Expected a spec-clean canonical artifact, got: {errors}"
    validated = SmokeTestResult.model_validate(content)
    assert validated.status == "passed"
    assert validated.output_file == "tmp/interactive-agy-smoke/todo-list.js"
    assert validated.observed_breaks == []
    assert "tool activity" in validated.headless_guide_checks
    assert "no output" not in (validated.observed_working or [])
    assert validated.summary


def test_agy_harness_writes_todo_list_with_expected_methods(
    cached_default_smoke: tuple[SmokeRunResult, Path],
) -> None:
    _result, workspace = cached_default_smoke
    todo_path = workspace / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    text = todo_path.read_text(encoding="utf-8")
    assert "function createTodoList" in text
    assert "module.exports" in text
    for method in ("add", "list", "complete", "remove"):
        assert method in text


def test_agy_harness_quota_branch_reports_provider_signal_and_terminal_classification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _run_agy_smoke_plumbing(tmp_path, monkeypatch, behavior="quota_exhausted")

    assert any("agy" in error for error in result.errors)
    assert any("RESOURCE_EXHAUSTED (code 429)" in error for error in result.errors)
    assert any("quota or rate limit is exhausted" in error for error in result.errors)
    assert not any("empty stdout by design" in error for error in result.errors)


@pytest.mark.parametrize(
    ("behavior", "expected_error", "forbidden_error"),
    (
        ("auth_failure", "authentication failed", "quota or rate limit is exhausted"),
        ("unknown_failure", "mock AGY unknown failure", "quota or rate limit is exhausted"),
    ),
)
def test_agy_harness_non_quota_failures_preserve_their_provider_signal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    behavior: str,
    expected_error: str,
    forbidden_error: str,
) -> None:
    result = _run_agy_smoke_plumbing(tmp_path, monkeypatch, behavior=behavior)

    assert any(expected_error in error for error in result.errors)
    assert not any(forbidden_error in error for error in result.errors)


def test_agy_harness_session_id_present_with_mock(
    cached_default_smoke: tuple[SmokeRunResult, Path],
) -> None:
    result, _workspace = cached_default_smoke
    assert result.session_id is not None
    assert result.session_id.startswith("interactive-agy-smoke-")


def test_agy_smoke_promotes_artifact_and_records_completion_sentinel(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    result = _run_agy_smoke_plumbing(
        tmp_path,
        monkeypatch,
        agent_name="agy/gemini-3.7-flash-medium",
    )
    assert result.artifact_submitted.holds is True
    assert result.explicit_completion_seen.holds is True
    assert result.file_created is True

    artifact_path = tmp_path / ".agent" / "artifacts" / "smoke_test_result.md"
    assert artifact_path.is_file(), f"Expected the promoted canonical artifact at {artifact_path}"

    todo_path = tmp_path / "tmp" / "interactive-agy-smoke" / "todo-list.js"
    assert todo_path.is_file(), f"Expected the mock-written todo file at {todo_path}"

    expected_run_id = resolve_smoke_harness_spec("agy/gemini-3.7-flash-medium").run_id
    assert artifact_receipt_present(tmp_path, expected_run_id, "smoke_test_result") is True, (
        f"Expected a canonical receipt for run_id={expected_run_id!r} "
        f"artifact_type='smoke_test_result'. The harness's "
        f"_is_smoke_artifact_submitted must call is_artifact_submitted -> "
        f"promote_fallback_artifact -> write_artifact_receipt to durably "
        f"stamp the receipt. Under RFC-013 P3 this lands as a row in "
        f"{tmp_path}/.agent/state.db (with the legacy file path preserved "
        f"as a durable fallback when DB persistence is unavailable)."
    )


_V1_1_13_BROKER_SECRET = "test-mock-agy-v1-1-13-broker-secret"

_V1_1_13_ONLY_VOCABULARY: tuple[str, ...] = (
    "call_mcp_tool",
    "system_message",
    "ralph_submit_md_artifact",
    "declare_complete",
)


def _capture_mock_stdout(
    artifact_dir: Path,
    capsys: pytest.CaptureFixture[str],
    *,
    v1_1_13: str | None,
) -> str:
    from tests._support import mock_agy

    env = os.environ.copy()
    env["MOCK_AGY_ARTIFACT_DIR"] = str(artifact_dir)
    env.pop("MOCK_AGY_SUBAGENT", None)
    env.pop("MOCK_AGY_ARTIFACT_DIR_OVERRIDE", None)
    env.pop("RALPH_MCP_ENDPOINT", None)
    env.pop("RALPH_MCP_RUN_ID", None)
    if v1_1_13 is None:
        env.pop("MOCK_AGY_V1_1_13", None)
    else:
        env["MOCK_AGY_V1_1_13"] = v1_1_13
    with unittest.mock.patch.dict(os.environ, env, clear=True):
        exit_code = mock_agy.main(
            [
                "--print",
                "--output-format",
                "stream-json",
                "--model",
                "gemini-3.6-flash-low",
            ]
        )
    assert exit_code == 0, f"mock AGY exited {exit_code}"
    return capsys.readouterr().out


def test_mock_v1_1_13_default_byte_compatible(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stdout_unset = _capture_mock_stdout(tmp_path / "unset", capsys, v1_1_13=None)
    stdout_zero = _capture_mock_stdout(tmp_path / "zero", capsys, v1_1_13="0")

    assert stdout_unset == stdout_zero, (
        "MOCK_AGY_V1_1_13=0 must be byte-compatible with the flag-unset default output"
    )
    for token in _V1_1_13_ONLY_VOCABULARY:
        assert token not in stdout_unset, (
            f"v1.1.13-only vocabulary {token!r} leaked into the default (flag-unset) mock output"
        )
    assert stdout_unset.strip(), "default mock output must not be empty"


def test_mock_v1_1_13_vocabulary_when_flag_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("MOCK_AGY_V1_1_13", "1")
    monkeypatch.setenv("RALPH_BROKER_SECRET", _V1_1_13_BROKER_SECRET)
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("MOCK_AGY_SUBAGENT", raising=False)

    result = _run_agy_smoke_plumbing(tmp_path, monkeypatch)

    stdout = _capture_mock_stdout(tmp_path / "transcript", capsys, v1_1_13="1")
    parser = AgyParser()
    events = list(parser.parse(iter(stdout.splitlines())))

    subagent_uses = [
        event
        for event in events
        if event.type == "tool_use" and event.content == "Todo Edge Case Researcher"
    ]
    subagent_results = [
        event
        for event in events
        if event.type == "tool_result" and event.content.startswith("Todo Edge Case Researcher")
    ]
    assert subagent_uses, "expected the invoke_subagent dispatch to parse as a tool_use"
    assert subagent_results, (
        "expected the invoke_subagent DONE frame to parse as a correlated tool_result"
    )

    system_message_events = [
        event for event in events if event.content == "agy step system_message"
    ]
    assert system_message_events, (
        "expected the bodiless system_message frame to surface as a lifecycle event"
    )

    def _event_pos(predicate: Callable[[Any], bool]) -> int:
        for index, event in enumerate(events):
            if predicate(event):
                return index
        return -1

    subagent_result_pos = _event_pos(
        lambda event: (
            event.type == "tool_result" and event.content.startswith("Todo Edge Case Researcher")
        )
    )
    system_message_pos = _event_pos(lambda event: event.content == "agy step system_message")
    submit_use_pos = _event_pos(
        lambda event: (
            event.type == "tool_use"
            and (event.metadata or {}).get("tool") == "call_mcp_tool"
            and (event.metadata or {}).get("tool_info", {}).get("parameters", {}).get("ToolName")
            == "ralph_submit_md_artifact"
        )
    )
    declare_pos = _event_pos(
        lambda event: (
            event.type == "tool_use"
            and (event.metadata or {}).get("tool") == "call_mcp_tool"
            and (event.metadata or {}).get("tool_info", {}).get("parameters", {}).get("ToolName")
            == "declare_complete"
        )
    )
    submit_result_pos = _event_pos(
        lambda event: (
            event.type == "tool_result"
            and (event.metadata or {}).get("tool") == "call_mcp_tool"
            and (event.metadata or {}).get("tool_info", {}).get("parameters", {}).get("ToolName")
            == "ralph_submit_md_artifact"
        )
    )
    assert -1 not in {subagent_result_pos, system_message_pos, submit_use_pos, declare_pos}, (
        "expected subagent result, system_message lifecycle, and both call_mcp_tool uses"
    )
    assert subagent_result_pos < system_message_pos < submit_use_pos < declare_pos, (
        "v1.1.13 frames must arrive in the measured order: subagent pair, "
        "system_message, ralph_submit_md_artifact pair, declare_complete pair"
    )
    assert submit_result_pos > submit_use_pos, "submit tool_result must follow its tool_use"

    run_id = resolve_smoke_harness_spec("agy/gemini-3.6-flash-low").run_id

    assert result.parsed_event_count >= 8, (
        f"expected at least 8 parser events, got {result.parsed_event_count}"
    )
    assert result.subagent_dispatch_seen is True
    assert result.subagent_result_seen is True
    assert result.subagent_dispatch_count >= 1
    assert result.artifact_submitted.holds is True
    assert result.explicit_completion_seen.holds is True

    assert (
        wire_evidence_for(
            tmp_path,
            run_id,
            tool_name="ralph_submit_md_artifact",
            secret=_V1_1_13_BROKER_SECRET,
        )
        is True
    ), "expected a verified wire-ledger tools/call record for ralph_submit_md_artifact"

    assert (
        _check_completion_sentinel(
            tmp_path,
            run_id,
            sentinel_secret=_V1_1_13_BROKER_SECRET,
        )
        is True
    ), "expected the durable, HMAC-verified completion sentinel for the run"

    assert DisplayCapability.SYNTAX_HIGHLIGHTING in result.observed_capabilities, (
        f"expected SYNTAX_HIGHLIGHTING to be observed, got {result.observed_capabilities}"
    )
    assert DisplayCapability.FILE_PREVIEW in result.observed_capabilities, (
        f"expected FILE_PREVIEW to be observed, got {result.observed_capabilities}"
    )
    assert not any("capability" in error.lower() for error in result.errors), (
        f"unexpected capability breaks: {result.errors}"
    )
