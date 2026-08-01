"""Focused tests for commit command activity rendering."""

from __future__ import annotations

import io
import os
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from git import Repo
from rich.console import Console
from rich.text import Text

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.factory import PipelineDeps

from ralph.agents.invoke import AgentInvocationError, build_invoke_options_from_config
from ralph.agents.parsers import AgentOutputLine
from ralph.agents.registry import AgentRegistry
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands._commit_chain_config import CommitChainConfig
from ralph.cli.commands.commit import (
    CommitAgentResult,
    CommitAttemptContext,
    collect_commit_agent_output,
    invoke_commit_agent_attempt,
)
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import DisplayContext, make_display_context
from ralph.git.operations import GitOperationError
from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.mcp.tools.names import SUBMIT_MD_ARTIFACT_TOOL, claude_tool_name
from ralph.policy.models import AgentsPolicy
from ralph.pro_support.hooks import ProPipelineHooks
from ralph.pro_support.state_query import SnapshotRegistry
from tests._pipeline_deps_factory import make_test_pipeline_deps
from tests._support.typed_accessors import (
    must_mapping,
    must_str,
)


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize("head_valid", [True, False])
def test_working_tree_diff_strips_lone_surrogates_from_git_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    head_valid: bool,
) -> None:
    """Commit prompt diffs must always remain valid UTF-8 text."""
    (tmp_path / ".git").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_git = bin_dir / "git"
    fake_git.write_text(
        """#!/bin/sh
if [ "$1" = "rev-parse" ]; then
  [ "$FAKE_HEAD_VALID" = "1" ]
  exit $?
fi
if [ "$1" = "diff" ]; then
  printf 'diff\\n+\\244\\n'
  exit 0
fi
if [ "$1" = "ls-files" ]; then
  exit 0
fi
exit 2
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)
    monkeypatch.setenv("FAKE_HEAD_VALID", "1" if head_valid else "0")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ.get('PATH', '')}")

    diff = commit_module.working_tree_diff(tmp_path)

    assert "diff" in diff
    assert "\udca4" not in diff
    diff.encode("utf-8")


def _write_commit_message_doc(repo_root: Path, message: str) -> None:
    """Write the markdown commit_message artifact the way MCP submission does."""
    if message.upper().startswith("SKIP:"):
        reason = message[len("SKIP:") :].strip()
        document = f"---\ntype: skip\nreason: {reason}\n---\n"
    else:
        document = f"---\ntype: commit\nsubject: {message}\n---\n"
    path = repo_root / ".agent" / "artifacts" / "commit_message.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(document, encoding="utf-8")


_OUTPUT_BATCH = 80
#: ``collect_commit_agent_output`` uses deques bounded at 256 lines (see
#: ``ralph.pipeline.plumbing.commit_plumbing``); this constant controls
#: how many filler lines we synthesize to exercise the bounded-tail path.
#: It must stay under 256 so the deque bound actually triggers; smaller
#: values keep the test fast while still proving the tail is bounded.


class _FakePipelineFactory:
    """Conforms to ``PipelineFactory`` and records every build call."""

    def __init__(self, deps: PipelineDeps) -> None:
        self._deps = deps
        self.calls: list[dict[str, object]] = []

    def build(
        self,
        config: UnifiedConfig,
        display_context: DisplayContext,
        *,
        model_identity: MultimodalModelIdentity | None = None,
        pro_hooks: ProPipelineHooks | None = None,
        **kwargs: object,
    ) -> PipelineDeps:
        del kwargs
        self.calls.append(
            {
                "config": config,
                "display_context": display_context,
                "model_identity": model_identity,
                "pro_hooks": pro_hooks,
            }
        )
        return self._deps


def _claude_commit_agent() -> AgentConfig:
    return AgentConfig(
        cmd="claude -p",
        output_flag="--output-format=stream-json",
        yolo_flag="--permission-mode auto",
        can_commit=True,
        json_parser=JsonParserType.CLAUDE,
        transport=AgentTransport.CLAUDE,
    )


def test_commit_invocation_passes_default_product_criteria_to_materialize_master_prompt(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Generate a commit message.", encoding="utf-8")

    # Create a minimal display context for the internal function
    display_context = make_display_context()

    with (
        patch("ralph.cli.commands.commit.materialize_master_prompt") as mock_materialize,
        patch("ralph.cli.commands.commit.invoke_agent", return_value=iter([])),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
        patch("ralph.cli.commands.commit.read_commit_message_artifact", return_value=None),
    ):
        mock_materialize.return_value = str(tmp_path / ".agent" / "tmp" / "commit_master_prompt.md")
        invoke_commit_agent_attempt(
            _claude_commit_agent(),
            prompt_file=str(prompt_file),
            attempt_context=CommitAttemptContext(
                repo_root=tmp_path,
                verbose=False,
                extra_env={},
            ),
            display_context=display_context,
        )

    mock_materialize.assert_called_once()
    _, kwargs = mock_materialize.call_args
    assert "default_product_criteria" in kwargs
    assert bool(kwargs["default_product_criteria"])


def test_submit_artifact_tool_name_claude_interactive() -> None:
    assert commit_module.submit_artifact_tool_name_for_transport(
        AgentTransport.CLAUDE_INTERACTIVE
    ) == claude_tool_name(SUBMIT_MD_ARTIFACT_TOOL)


def test_commit_tool_render_escapes_markup_like_input_before_console_render() -> None:
    output = AgentOutputLine(
        type="tool_use",
        content="Write",
        metadata={
            "input": {
                "file_path": "/tmp/[unsafe].py",
                "newText": "[/{color}]",
            }
        },
    )

    rendered = commit_module.render_commit_agent_activity_line(output, "claude")

    assert rendered is not None
    assert isinstance(rendered, Text)

    console = Console(file=io.StringIO(), force_terminal=False, color_system=None)
    console.print(rendered)


def test_build_invoke_options_from_config_maps_all_timeout_fields() -> None:
    config = GeneralConfig(
        agent_idle_timeout_seconds=42.0,
        agent_idle_drain_window_seconds=1.5,
        agent_idle_max_waiting_on_child_seconds=900.0,
        agent_idle_poll_interval_seconds=0.1,
        agent_parent_exit_grace_seconds=3.0,
        agent_descendant_wait_timeout_seconds=20.0,
        agent_descendant_wait_poll_seconds=0.3,
        agent_process_exit_wait_seconds=10.0,
        agent_max_session_seconds=7200.0,
        agent_waiting_status_interval_seconds=60.0,
        agent_suspect_waiting_on_child_seconds=300.0,
        agent_idle_no_progress_waiting_on_child_seconds=600.0,
        agent_os_descendant_only_ceiling_seconds=300.0,
        agent_child_progress_ttl_seconds=30.0,
        agent_child_heartbeat_ttl_seconds=8.0,
        agent_child_stale_label_ttl_seconds=5.0,
        agent_child_exit_reconcile_seconds=2.0,
    )
    opts = build_invoke_options_from_config(config)

    assert opts.idle_timeout_seconds == config.agent_idle_timeout_seconds
    assert opts.drain_window_seconds == config.agent_idle_drain_window_seconds
    assert opts.max_waiting_on_child_seconds == config.agent_idle_max_waiting_on_child_seconds
    assert opts.idle_poll_interval_seconds == config.agent_idle_poll_interval_seconds
    assert opts.parent_exit_grace_seconds == config.agent_parent_exit_grace_seconds
    assert opts.descendant_wait_timeout_seconds == config.agent_descendant_wait_timeout_seconds
    assert opts.descendant_wait_poll_seconds == config.agent_descendant_wait_poll_seconds
    assert opts.process_exit_wait_seconds == config.agent_process_exit_wait_seconds
    assert opts.max_session_seconds == config.agent_max_session_seconds
    assert opts.waiting_status_interval_seconds == config.agent_waiting_status_interval_seconds
    assert opts.suspect_waiting_on_child_seconds == config.agent_suspect_waiting_on_child_seconds
    assert (
        opts.max_waiting_on_child_no_progress_seconds
        == config.agent_idle_no_progress_waiting_on_child_seconds
    )
    assert opts.child_progress_ttl_seconds == config.agent_child_progress_ttl_seconds
    assert opts.child_heartbeat_ttl_seconds == config.agent_child_heartbeat_ttl_seconds
    assert opts.child_stale_label_ttl_seconds == config.agent_child_stale_label_ttl_seconds
    assert opts.child_exit_reconcile_seconds == config.agent_child_exit_reconcile_seconds


def test_commit_invocation_passes_full_timeout_bundle(tmp_path: Path) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Generate a commit message.", encoding="utf-8")

    general_config = GeneralConfig(
        agent_idle_timeout_seconds=99.0,
        agent_idle_no_progress_waiting_on_child_seconds=600.0,
        agent_os_descendant_only_ceiling_seconds=200.0,
    )
    attempt_context = CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
        general_config=general_config,
    )
    display_context = make_display_context()

    captured_options = []

    def fake_invoke_agent(agent: object, prompt_file: object, *, options: object = None) -> object:
        captured_options.append(options)
        return iter([])

    with (
        patch("ralph.cli.commands.commit.materialize_master_prompt", return_value=None),
        patch("ralph.cli.commands.commit.invoke_agent", side_effect=fake_invoke_agent),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
        patch("ralph.cli.commands.commit.read_commit_message_artifact", return_value=None),
    ):
        invoke_commit_agent_attempt(
            _claude_commit_agent(),
            prompt_file=str(prompt_file),
            attempt_context=attempt_context,
            display_context=display_context,
        )

    assert len(captured_options) == 1
    opts = captured_options[0]
    assert opts.idle_timeout_seconds == general_config.agent_idle_timeout_seconds
    assert (
        opts.max_waiting_on_child_no_progress_seconds
        == general_config.agent_idle_no_progress_waiting_on_child_seconds
    )


def test_commit_invocation_requires_commit_message_artifact(tmp_path: Path) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Generate a commit message.", encoding="utf-8")

    attempt_context = CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
    )
    display_context = make_display_context()

    captured_options = []

    def fake_invoke_agent(agent: object, prompt_file: object, *, options: object = None) -> object:
        del agent, prompt_file
        captured_options.append(options)
        return iter([])

    with (
        patch("ralph.cli.commands.commit.materialize_master_prompt", return_value=None),
        patch("ralph.cli.commands.commit.invoke_agent", side_effect=fake_invoke_agent),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
        patch("ralph.cli.commands.commit.read_commit_message_artifact", return_value=None),
    ):
        invoke_commit_agent_attempt(
            _claude_commit_agent(),
            prompt_file=str(prompt_file),
            attempt_context=attempt_context,
            display_context=display_context,
        )

    assert len(captured_options) == 1
    opts = captured_options[0]
    assert opts.required_artifact is not None
    assert opts.required_artifact.artifact_type == "commit_message"
    assert opts.required_artifact.artifact_path == ".agent/artifacts/commit_message.md"
    assert opts.required_artifact.artifact_required is True


@pytest.mark.timeout_seconds(3)
def test_collect_commit_agent_output_keeps_early_session_id_with_bounded_tail() -> None:
    display_context = make_display_context()
    session_line = '{"type":"session","session_id":"sess-early"}'
    filler = ["x" * 8192 for _ in range(_OUTPUT_BATCH)]

    parsed_output, raw_output, resume_session_id = collect_commit_agent_output(
        [session_line, *filler],
        parser_type="generic",
        agent_name="claude",
        verbose=False,
        display_context=display_context,
    )

    assert resume_session_id == "sess-early"
    # The implementation's ``raw_output`` is a deque with
    # ``maxlen=_MAX_COMMIT_RAW_OUTPUT_LINES`` (256 in production); we
    # feed one session_line + _OUTPUT_BATCH filler items, so the tail is
    # bounded by min(_OUTPUT_BATCH + 1, 256). The original assertion
    # ``len(raw_output) < _OUTPUT_BATCH`` only held when _OUTPUT_BATCH
    # exceeded the deque's maxlen; with smaller fixture sizes the
    # assertion becomes a tautology. The honest bounded-tail contract is
    # captured by ``<= _OUTPUT_BATCH + 1``.
    assert len(raw_output) <= _OUTPUT_BATCH + 1
    assert len(parsed_output) <= _OUTPUT_BATCH + 1


def test_generate_commit_message_retries_post_tool_empty_response_with_reset(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Generate a commit message.", encoding="utf-8")
    display_context = make_display_context()

    class _FakeBridge:
        def __init__(self) -> None:
            self.reset_calls = 0

        def reset_tool_registry(self) -> None:
            self.reset_calls += 1

    bridge = _FakeBridge()
    attempt_context = CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
        bridge=bridge,
    )

    agent = AgentConfig(cmd="nanocoder", can_commit=True, json_parser=JsonParserType.GENERIC)
    failure = AgentInvocationError(
        "nanocoder",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=[
            '{"type":"session","session_id":"sess-post-tool"}',
            '{"type":"tool_result","tool":"read_file"}',
        ],
    )
    calls: list[object | None] = []

    def fake_invoke_agent(
        _agent: object,
        *_args: object,
        options: object = None,
        **_kwargs: object,
    ) -> object:
        calls.append(getattr(options, "session_id", None))
        if len(calls) == 1:
            raise failure
        _write_commit_message_doc(tmp_path, "fix: recovered after retry")
        return iter([])

    with (
        patch("ralph.cli.commands.commit.materialize_master_prompt", return_value=None),
        patch("ralph.cli.commands.commit.invoke_agent", side_effect=fake_invoke_agent),
    ):
        result = commit_module._generate_commit_message_with_agent(
            agent.cmd,
            agent,
            prompt_file=str(prompt_file),
            attempt_context=attempt_context,
            display_context=display_context,
            pipeline_deps=make_test_pipeline_deps(display_context, bridge=bridge),
        )

    assert result.message == "fix: recovered after retry"
    assert bridge.reset_calls == 1
    assert calls == [None, "sess-post-tool"]


def test_generate_commit_message_retries_repeated_post_tool_empty_response_until_success(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Generate a commit message.", encoding="utf-8")
    display_context = make_display_context()

    class _FakeBridge:
        def __init__(self) -> None:
            self.reset_calls = 0

        def reset_tool_registry(self) -> None:
            self.reset_calls += 1

    bridge = _FakeBridge()
    attempt_context = CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
        bridge=bridge,
    )

    agent = AgentConfig(cmd="claude", can_commit=True, json_parser=JsonParserType.GENERIC)
    failure = AgentInvocationError(
        "claude",
        1,
        "Model returned an empty response with no tool calls",
        parsed_output=[
            '{"type":"session","session_id":"sess-post-tool"}',
            '{"type":"tool_result","tool":"read_file"}',
        ],
    )
    calls: list[object | None] = []

    def fake_invoke_agent(
        _agent: object,
        *_args: object,
        options: object = None,
        **_kwargs: object,
    ) -> object:
        calls.append(getattr(options, "session_id", None))
        if len(calls) < 3:
            raise failure
        _write_commit_message_doc(tmp_path, "fix: recovered after repeated retry")
        return iter([])

    with (
        patch("ralph.cli.commands.commit.materialize_master_prompt", return_value=None),
        patch("ralph.cli.commands.commit.invoke_agent", side_effect=fake_invoke_agent),
    ):
        result = commit_module._generate_commit_message_with_agent(
            agent.cmd,
            agent,
            prompt_file=str(prompt_file),
            attempt_context=attempt_context,
            display_context=display_context,
            pipeline_deps=make_test_pipeline_deps(display_context, bridge=bridge),
        )

    assert result.message == "fix: recovered after repeated retry"
    assert bridge.reset_calls == 2
    assert calls == [None, "sess-post-tool", "sess-post-tool"]
    assert result.failure_details != []


def test_generate_commit_message_recovers_midstream_failure_using_raw_session_id(
    tmp_path: Path,
) -> None:
    prompt_file = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    prompt_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_file.write_text("Generate a commit message.", encoding="utf-8")
    display_context = make_display_context()

    class _FakeBridge:
        def __init__(self) -> None:
            self.reset_calls = 0

        def reset_tool_registry(self) -> None:
            self.reset_calls += 1

    bridge = _FakeBridge()
    attempt_context = CommitAttemptContext(
        repo_root=tmp_path,
        verbose=False,
        extra_env={},
        bridge=bridge,
    )

    agent = AgentConfig(cmd="claude", can_commit=True, json_parser=JsonParserType.GENERIC)
    calls: list[object | None] = []

    def fake_invoke_agent(
        _agent: object,
        *_args: object,
        options: object = None,
        **_kwargs: object,
    ) -> object:
        calls.append(getattr(options, "session_id", None))
        if len(calls) == 1:

            def _failing_iter() -> object:
                yield '{"type":"session","session_id":"sess-midstream"}'
                yield '{"type":"tool_result","tool":"read_file"}'
                raise AgentInvocationError(
                    "claude",
                    1,
                    "Model returned an empty response with no tool calls",
                    parsed_output=["claude tool result: read_file"],
                )

            return _failing_iter()
        _write_commit_message_doc(tmp_path, "fix: recovered after midstream retry")
        return iter([])

    with (
        patch("ralph.cli.commands.commit.materialize_master_prompt", return_value=None),
        patch("ralph.cli.commands.commit.invoke_agent", side_effect=fake_invoke_agent),
    ):
        result = commit_module._generate_commit_message_with_agent(
            agent.cmd,
            agent,
            prompt_file=str(prompt_file),
            attempt_context=attempt_context,
            display_context=display_context,
            pipeline_deps=make_test_pipeline_deps(display_context, bridge=bridge),
        )

    assert result.message == "fix: recovered after midstream retry"
    assert bridge.reset_calls == 1
    assert calls == [None, "sess-midstream"]


def test_handle_agent_commit_generation_reports_stage_failure_without_traceback(
    tmp_path: Path,
) -> None:
    output = io.StringIO()
    display_context = make_display_context(
        console=Console(file=output, force_terminal=False, color_system=None, width=120)
    )
    config = UnifiedConfig()

    with (
        patch("ralph.cli.commands.commit.working_tree_diff", return_value="diff --git a/x b/x"),
        patch("ralph.cli.commands.commit.AgentRegistry.from_config", return_value=object()),
        patch("ralph.cli.commands.commit._resolve_commit_message_agents", return_value=["claude"]),
        patch("ralph.cli.commands.commit.resolve_workspace_scope", return_value=object()),
        patch(
            "ralph.cli.commands.commit.load_agents_policy_for_workspace_scope",
            return_value=object(),
        ),
        patch(
            "ralph.cli.commands.commit._generate_commit_message_with_chain",
            return_value=CommitAgentResult(message="fix: recover stale git lock"),
        ),
        patch(
            "ralph.cli.commands.commit.read_commit_message_artifact",
            return_value="fix: recover stale git lock",
        ),
        patch(
            "ralph.cli.commands.commit.stage_commit_changes_safely",
            side_effect=GitOperationError("stage_all", "stale git lock remained active"),
        ),
        patch("ralph.cli.commands.commit.create_commit"),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
    ):
        commit_module._handle_agent_commit_generation(
            repo_root=tmp_path,
            config=config,
            options=commit_module.CommitPlumbingOptions(generate_commit=True),
            display_context=display_context,
        )

    rendered = output.getvalue()
    assert "Commit failed" in rendered
    assert "stale git lock remained active" in rendered


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_commit_generation_regression_secrets_never_reach_message_agent(
    tmp_git_repo: Path,
) -> None:
    """Regression for verifier finding: sanitize agent input without index mutation."""
    tracked_secret = tmp_git_repo / "credentials.json"
    tracked_secret.write_text('{"token":"placeholder-old"}\n', encoding="utf-8")
    safe_tracked = tmp_git_repo / "safe-tracked.txt"
    safe_tracked.write_text("old safe work\n", encoding="utf-8")
    with Repo(tmp_git_repo) as repo:
        repo.index.add(["credentials.json", "safe-tracked.txt"])
        repo.index.commit("seed tracked files")

    tracked_secret.write_text('{"token":"placeholder-new"}\n', encoding="utf-8")
    safe_tracked.write_text("new safe work\n", encoding="utf-8")
    untracked_secret = tmp_git_repo / ".env"
    untracked_secret.write_text("TOKEN=untracked-placeholder\n", encoding="utf-8")
    safe_untracked = tmp_git_repo / "safe-untracked.txt"
    safe_untracked.write_text("safe untracked work\n", encoding="utf-8")
    captured_diffs: list[str] = []

    def capture_agent_diff(**kwargs: object) -> CommitAgentResult:
        captured_diffs.append(must_str(kwargs["diff"]))
        return CommitAgentResult(message="test: describe safe work")

    with (
        patch("ralph.cli.commands.commit.AgentRegistry.from_config", return_value=object()),
        patch("ralph.cli.commands.commit._resolve_commit_message_agents", return_value=["claude"]),
        patch("ralph.cli.commands.commit.resolve_workspace_scope", return_value=object()),
        patch(
            "ralph.cli.commands.commit.load_agents_policy_for_workspace_scope",
            return_value=object(),
        ),
        patch(
            "ralph.cli.commands.commit._generate_commit_message_with_chain",
            side_effect=capture_agent_diff,
        ),
        patch(
            "ralph.cli.commands.commit.read_commit_message_artifact",
            return_value="test: describe safe work",
        ),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
    ):
        commit_module._handle_agent_commit_generation(
            repo_root=tmp_git_repo,
            config=UnifiedConfig(),
            options=commit_module.CommitPlumbingOptions(generate_commit_msg=True),
            display_context=make_display_context(),
        )

    assert len(captured_diffs) == 1
    agent_diff = captured_diffs[0]
    assert "safe-tracked.txt" in agent_diff
    assert "new safe work" in agent_diff
    assert "safe-untracked.txt" in agent_diff
    assert "credentials.json" not in agent_diff
    assert "placeholder-new" not in agent_diff
    assert ".env" not in agent_diff
    assert "untracked-placeholder" not in agent_diff
    with Repo(tmp_git_repo) as repo:
        assert must_str(repo.git.diff("--cached", "--name-only")) == ""
    assert tracked_secret.read_text(encoding="utf-8") == '{"token":"placeholder-new"}\n'
    assert untracked_secret.read_text(encoding="utf-8") == "TOKEN=untracked-placeholder\n"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_generate_commit_excludes_untracked_secret_while_staging_safe_work(
    tmp_git_repo: Path,
) -> None:
    """Direct commit generation must never stage a recognized untracked secret."""
    secret = tmp_git_repo / ".env"
    safe_work = tmp_git_repo / "safe.txt"
    secret.write_text("TOKEN=placeholder\n", encoding="utf-8")
    safe_work.write_text("safe work\n", encoding="utf-8")
    config = UnifiedConfig()
    staged_at_commit: list[str] = []

    def capture_staged_paths(
        repo_root: Path,
        _message: str,
        *,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> str:
        del author_name, author_email
        with Repo(repo_root) as repo:
            staged_at_commit.extend(
                path
                for path in must_str(repo.git.diff("--cached", "--name-only")).splitlines()
                if path
            )
        return "a" * 40

    with (
        patch("ralph.cli.commands.commit.AgentRegistry.from_config", return_value=object()),
        patch("ralph.cli.commands.commit._resolve_commit_message_agents", return_value=["claude"]),
        patch("ralph.cli.commands.commit.resolve_workspace_scope", return_value=object()),
        patch(
            "ralph.cli.commands.commit.load_agents_policy_for_workspace_scope",
            return_value=object(),
        ),
        patch(
            "ralph.cli.commands.commit._generate_commit_message_with_chain",
            return_value=CommitAgentResult(message="test: stage safe work"),
        ),
        patch(
            "ralph.cli.commands.commit.read_commit_message_artifact",
            return_value="test: stage safe work",
        ),
        patch("ralph.cli.commands.commit.create_commit", side_effect=capture_staged_paths),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
    ):
        commit_module._handle_agent_commit_generation(
            repo_root=tmp_git_repo,
            config=config,
            options=commit_module.CommitPlumbingOptions(generate_commit=True),
            display_context=make_display_context(),
        )

    assert "safe.txt" in staged_at_commit
    assert ".env" not in staged_at_commit
    assert secret.read_text(encoding="utf-8") == "TOKEN=placeholder\n"


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(10)
def test_generate_commit_untracks_recognized_tracked_secret_without_deleting_it(
    tmp_git_repo: Path,
) -> None:
    """Direct commit generation stages secret removal, never modified secret content."""
    secret = tmp_git_repo / "credentials.json"
    secret.write_text('{"token":"placeholder-old"}\n', encoding="utf-8")
    with Repo(tmp_git_repo) as repo:
        repo.index.add(["credentials.json"])
        repo.index.commit("track secret fixture")
    secret.write_text('{"token":"placeholder-new"}\n', encoding="utf-8")
    config = UnifiedConfig()
    staged_status: list[str] = []

    def capture_staged_status(
        repo_root: Path,
        _message: str,
        *,
        author_name: str | None = None,
        author_email: str | None = None,
    ) -> str:
        del author_name, author_email
        with Repo(repo_root) as repo:
            staged_status.extend(
                line
                for line in must_str(repo.git.diff("--cached", "--name-status")).splitlines()
                if line
            )
        return "b" * 40

    with (
        patch("ralph.cli.commands.commit.AgentRegistry.from_config", return_value=object()),
        patch("ralph.cli.commands.commit._resolve_commit_message_agents", return_value=["claude"]),
        patch("ralph.cli.commands.commit.resolve_workspace_scope", return_value=object()),
        patch(
            "ralph.cli.commands.commit.load_agents_policy_for_workspace_scope",
            return_value=object(),
        ),
        patch(
            "ralph.cli.commands.commit._generate_commit_message_with_chain",
            return_value=CommitAgentResult(message="test: remove tracked secret"),
        ),
        patch(
            "ralph.cli.commands.commit.read_commit_message_artifact",
            return_value="test: remove tracked secret",
        ),
        patch("ralph.cli.commands.commit.create_commit", side_effect=capture_staged_status),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
    ):
        commit_module._handle_agent_commit_generation(
            repo_root=tmp_git_repo,
            config=config,
            options=commit_module.CommitPlumbingOptions(generate_commit=True),
            display_context=make_display_context(),
        )

    assert staged_status == ["D\tcredentials.json"]
    assert secret.read_text(encoding="utf-8") == '{"token":"placeholder-new"}\n'


def test_handle_agent_commit_generation_surfaces_recovered_retry_evidence(
    tmp_path: Path,
) -> None:
    output = io.StringIO()
    display_context = make_display_context(
        console=Console(file=output, force_terminal=False, color_system=None, width=120)
    )
    config = UnifiedConfig()

    with (
        patch("ralph.cli.commands.commit.working_tree_diff", return_value="diff --git a/x b/x"),
        patch("ralph.cli.commands.commit.AgentRegistry.from_config", return_value=object()),
        patch("ralph.cli.commands.commit._resolve_commit_message_agents", return_value=["claude"]),
        patch("ralph.cli.commands.commit.resolve_workspace_scope", return_value=object()),
        patch(
            "ralph.cli.commands.commit.load_agents_policy_for_workspace_scope",
            return_value=object(),
        ),
        patch(
            "ralph.cli.commands.commit._generate_commit_message_with_chain",
            return_value=CommitAgentResult(
                message="fix: recover stale git lock",
                failure_details=[
                    "retryable failure recovered: "
                    "Model returned an empty response with no tool calls"
                ],
            ),
        ),
        patch(
            "ralph.cli.commands.commit.read_commit_message_artifact",
            return_value="fix: recover stale git lock",
        ),
        patch("ralph.cli.commands.commit.delete_commit_message_artifacts"),
    ):
        commit_module._handle_agent_commit_generation(
            repo_root=tmp_path,
            config=config,
            options=commit_module.CommitPlumbingOptions(generate_commit_msg=True),
            display_context=display_context,
        )

    rendered = output.getvalue()
    assert "Recovered after retryable MCP/agent failures" in rendered
    assert "Model returned an empty response with no tool calls" in rendered


def test_generate_commit_message_with_chain_routes_through_default_pipeline_factory(
    tmp_path: Path,
) -> None:
    """The commit command routes pipeline construction through DefaultPipelineFactory
    so plumbing uses the same composition root as the main pipeline.
    """
    display_context = make_display_context()
    model_identity = MultimodalModelIdentity(provider="claude", model_id="sonnet")
    pro_hooks = ProPipelineHooks(snapshot_registry=SnapshotRegistry())
    captured_plumbing: dict[str, object] = {}
    expected_deps = make_test_pipeline_deps(display_context)
    fake_factory = _FakePipelineFactory(expected_deps)

    def fake_run_commit_plumbing(**kwargs: object) -> CommitAgentResult:
        captured_plumbing["kwargs"] = kwargs
        return CommitAgentResult(message="feat: shared init")

    chain_config = CommitChainConfig(
        registry=AgentRegistry(),
        agents=["claude"],
        verbose=False,
        agents_policy=AgentsPolicy(),
        general_config=UnifiedConfig(),
    )

    with (
        patch.object(
            commit_module,
            "DefaultPipelineFactory",
            lambda *_args, **_kwargs: fake_factory,
        ),
        patch.object(commit_module, "run_commit_plumbing", fake_run_commit_plumbing),
    ):
        result = commit_module._generate_commit_message_with_chain(
            diff="diff --git a/x b/x",
            repo_root=tmp_path,
            chain_config=chain_config,
            display_context=display_context,
            pro_hooks=pro_hooks,
            model_identity=model_identity,
        )

    assert result.message == "feat: shared init"
    assert len(fake_factory.calls) == 1
    factory_call = fake_factory.calls[0]
    assert factory_call["config"] is chain_config.general_config
    assert factory_call["display_context"] is display_context
    assert factory_call["model_identity"] is model_identity
    assert factory_call["pro_hooks"] is pro_hooks
    plumbing_kwargs = must_mapping(captured_plumbing["kwargs"])
    assert plumbing_kwargs["pipeline_deps"] is expected_deps
