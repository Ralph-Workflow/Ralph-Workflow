"""Focused CLI command tests for commit, diagnose, init, and option helpers."""

from __future__ import annotations

import json
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import rich_click as click
from rich.console import Console

from ralph.cli import options as options_module
from ralph.cli.commands import commit as commit_module
from ralph.cli.commands import diagnose as diagnose_module
from ralph.cli.commands import init as init_module
from ralph.config.enums import AgentTransport, JsonParserType, ReviewDepth, Verbosity
from ralph.config.models import AgentConfig, UnifiedConfig
from ralph.mcp.commit_message import write_commit_message_artifact
from ralph.mcp.session import AgentSession
from ralph.mcp.tool_bridge import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest


_SUMMARY_RETRY_FAILURES = 2


def _attach_console(monkeypatch: pytest.MonkeyPatch, module: object) -> StringIO:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None)
    monkeypatch.setattr(module, "console", console)
    return stream


def _simple_config() -> SimpleNamespace:
    return SimpleNamespace(
        general=SimpleNamespace(
            git_user_name="user",
            git_user_email="user@example.com",
            verbosity=2,
        ),
        agent_drains={"commit": "commit_chain", "review": "review_chain"},
        agent_chains={"commit_chain": ["commit_agent"], "review_chain": ["review_agent"]},
    )


def _stub_commit_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBridge:
        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(commit_module, "_start_commit_bridge", lambda _repo_root: FakeBridge())


def test_start_commit_bridge_exposes_write_file_for_commit_session(tmp_path: Path) -> None:
    session = AgentSession(
        session_id="commit-session",
        run_id="commit-run",
        drain="commit",
        capabilities={
            "ArtifactSubmit",
            "RunReportProgress",
            "WorkspaceRead",
            "WorkspaceWriteEphemeral",
        },
    )
    registry = build_ralph_tool_registry(session, FsWorkspace(tmp_path))
    tool_names = {definition.name for definition in registry.list_definitions()}

    assert {"write_file", "read_file", "ralph_submit_artifact"}.issubset(tool_names)


def _artifact_invoke(repo_root: Path, message: str):
    def _invoke(*_args, **_kwargs):
        write_commit_message_artifact(repo_root, message)
        return iter([])

    return _invoke


def test_commit_plumbing_reports_missing_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)

    def raise_repo() -> Path:
        raise RuntimeError("no repo")

    monkeypatch.setattr(commit_module, "find_repo_root", raise_repo)
    commit_module.commit_plumbing()
    assert "Not in a git repository" in stream.getvalue()


def test_commit_plumbing_reports_config_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: Path("/tmp"))

    def raise_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(commit_module, "load_config", raise_config)
    commit_module.commit_plumbing()
    assert "Error loading config" in stream.getvalue()


def test_commit_plumbing_prints_no_staged_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: Path("/tmp"))
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(commit_module, "has_staged_changes", lambda root: False)

    commit_module.commit_plumbing()
    assert "No staged changes to commit" in stream.getvalue()


def test_commit_plumbing_injects_workspace_scope_for_implicit_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    scope = WorkspaceScope("/tmp/worktree")
    captured: dict[str, object] = {}

    monkeypatch.setattr(commit_module, "find_repo_root", lambda: Path("/tmp/worktree"))
    monkeypatch.setattr(commit_module, "resolve_workspace_scope", lambda _start: scope)

    def fake_load_config(*args: object, **kwargs: object) -> SimpleNamespace:
        captured["kwargs"] = kwargs
        return _simple_config()

    monkeypatch.setattr(commit_module, "load_config", fake_load_config)
    monkeypatch.setattr(commit_module, "has_staged_changes", lambda _root: False)

    commit_module.commit_plumbing()

    assert "No staged changes to commit" in stream.getvalue()
    assert captured["kwargs"] == {"workspace_scope": scope}


def test_generate_commit_stages_working_tree_changes_when_nothing_is_staged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    _stub_commit_bridge(monkeypatch)

    staged_all_calls: list[Path] = []

    def fake_stage_all(repo_root: Path) -> None:
        staged_all_calls.append(repo_root)

    monkeypatch.setattr(commit_module, "stage_all", fake_stage_all, raising=False)
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(
        commit_module, "invoke_agent", _artifact_invoke(tmp_path, "fix: generated by agent")
    )

    commit_calls: list[str] = []

    def fake_create_commit(
        _repo_root: Path,
        _message: str,
        author_name: str | None,
        author_email: str | None,
    ) -> str:
        commit_calls.append(f"{author_name}:{author_email}")
        return "cafebabe1234"

    monkeypatch.setattr(commit_module, "create_commit", fake_create_commit)

    commit_module.commit_plumbing(options=commit_module.CommitPlumbingOptions(generate_commit=True))

    assert staged_all_calls == [tmp_path]
    assert commit_calls == ["user:user@example.com"]
    output = stream.getvalue()
    assert "Created commit" in output
    assert "No staged changes to commit" not in output


def test_generate_commit_uses_commit_drain_agent_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    _stub_commit_bridge(monkeypatch)
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )

    invoked_agents: list[str] = []

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, name: str):
            cmd = "codex" if name == "commit_agent" else "claude -p"
            return AgentConfig(
                cmd=cmd,
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    def fake_invoke_agent(agent_config, *_args, **_kwargs):
        invoked_agents.append(agent_config.cmd)
        write_commit_message_artifact(tmp_path, "fix: commit drain message")
        return iter([])

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert invoked_agents == ["codex"]
    output = stream.getvalue()
    assert "Generated commit message" in output
    assert "fix: commit drain message" in output


def test_generate_commit_uses_direct_opencode_model_from_commit_drain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        commit_module,
        "load_config",
        lambda *args, **kwargs: UnifiedConfig(
            agent_chains={
                "commit_chain": ["opencode/minimax/MiniMax-M2.7-highspeed"],
            },
            agent_drains={"commit": "commit_chain", "review": "commit_chain"},
        ),
    )
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    invoked_model_flags: list[str | None] = []

    def fake_invoke_agent(agent_config, *_args, **_kwargs):
        invoked_model_flags.append(agent_config.model_flag)
        write_commit_message_artifact(tmp_path, "fix: commit drain message")
        return iter([])

    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert invoked_model_flags == ["-m minimax/MiniMax-M2.7-highspeed"]
    output = stream.getvalue()
    assert "Generated commit message" in output
    assert "fix: commit drain message" in output


def test_generate_commit_retries_missing_artifact_in_same_session_when_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module,
        "resolve_workspace_scope",
        lambda _start: WorkspaceScope(tmp_path),
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="claude -p",
                output_flag="--output-format=stream-json",
                print_flag="--print",
                streaming_flag="--include-partial-messages",
                session_flag="--resume {}",
                can_commit=True,
                json_parser=JsonParserType.CLAUDE,
                transport=AgentTransport.CLAUDE,
            )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)

    seen_session_ids: list[str | None] = []

    def fake_invoke_agent(_agent_config, *_args, **kwargs):
        options = kwargs.get("options")
        seen_session_ids.append(None if options is None else options.session_id)
        if len(seen_session_ids) == 1:
            return iter(['{"session_id":"claude-session-1"}'])
        write_commit_message_artifact(tmp_path, "fix: retried in session")
        return iter([])

    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert seen_session_ids == [None, "claude-session-1"]
    output = stream.getvalue()
    assert "Generated commit message" in output
    assert "fix: retried in session" in output


def test_generate_commit_retries_with_summarized_failure_before_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        commit_module,
        "load_config",
        lambda *args, **kwargs: UnifiedConfig(
            agent_chains={"commit_chain": ["claude", "opencode/minimax/MiniMax-M2.7-highspeed"]},
            agent_drains={"commit": "commit_chain", "review": "commit_chain"},
        ),
    )
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    _stub_commit_bridge(monkeypatch)

    prompt_bodies: list[str] = []

    def fake_write_commit_prompt_file(_root: Path, prompt: str) -> str:
        prompt_bodies.append(prompt)
        prompt_file = tmp_path / f"prompt-{len(prompt_bodies)}.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        return str(prompt_file)

    monkeypatch.setattr(commit_module, "_write_commit_prompt_file", fake_write_commit_prompt_file)

    invoked_agents: list[tuple[str, str | None]] = []

    def fake_invoke_agent(agent_config, prompt_file, *_args, **kwargs):
        options = kwargs.get("options")
        invoked_agents.append((agent_config.cmd, None if options is None else options.session_id))
        if len(invoked_agents) <= _SUMMARY_RETRY_FAILURES:
            return iter(["claude: This is a commit prompt file requesting a commit message"])
        write_commit_message_artifact(tmp_path, "fix: fallback agent message")
        return iter([])

    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert invoked_agents == [
        ("claude -p", None),
        ("claude -p", None),
        ("opencode", None),
    ]
    assert any(
        "Previous attempt failed to submit the required commit_message artifact" in body
        for body in prompt_bodies[1:]
    )
    assert any(
        'Call the submit-artifact MCP tool with artifact_type="commit_message"' in body
        for body in prompt_bodies[1:]
    )
    assert any(
        "If the submit-artifact MCP tool is still unavailable, write the raw commit payload JSON "
        "to .agent/tmp/commit_message.json" in body
        for body in prompt_bodies[1:]
    )
    assert any("Do not use content_path for this retry" in body for body in prompt_bodies[1:])
    assert any("Message quality mistakes to avoid" in body for body in prompt_bodies[1:])
    assert any("Bad: chore: update files" in body for body in prompt_bodies[1:])
    assert any(
        "Good: feat(mcp): add structured commit retries" in body for body in prompt_bodies[1:]
    )
    assert any("Bad: fix: stuff" in body for body in prompt_bodies[1:])
    assert any(
        "Good: fix(parser): preserve prefixed transcript lines" in body
        for body in prompt_bodies[1:]
    )
    output = stream.getvalue()
    assert "Generated commit message" in output
    assert "fix: fallback agent message" in output


def test_generate_commit_passes_mcp_endpoint_to_opencode_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="opencode",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.OPENCODE,
            )

    seen_extra_env: list[dict[str, str] | None] = []

    def fake_invoke_agent(_agent_config, *_args, **kwargs):
        options = kwargs.get("options")
        seen_extra_env.append(None if options is None else options.extra_env)
        assert options is not None and options.pure is True
        write_commit_message_artifact(tmp_path, "fix: commit drain message")
        return iter([])

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert seen_extra_env == [
        {
            "RALPH_MCP_ENDPOINT": "http://127.0.0.1:9999/mcp",
            "RALPH_MCP_RUN_ID": "commit-plumbing",
        }
    ]
    assert "Generated commit message" in stream.getvalue()


def test_generate_commit_prompt_mentions_opencode_prefixed_submit_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    captured_prompt: list[str] = []

    def fake_write_commit_prompt_file(_root: Path, prompt: str) -> str:
        captured_prompt.append(prompt)
        return "PROMPT.md"

    monkeypatch.setattr(commit_module, "_write_commit_prompt_file", fake_write_commit_prompt_file)
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="opencode",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.OPENCODE,
            )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(
        commit_module, "invoke_agent", _artifact_invoke(tmp_path, "fix: generated by agent")
    )

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert captured_prompt
    assert "ralph_submit_artifact" in captured_prompt[0]
    assert "ralph_ralph_submit_artifact" not in captured_prompt[0]


def test_generate_commit_prompt_mentions_claude_namespaced_submit_tool(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    captured_prompt: list[str] = []

    def fake_write_commit_prompt_file(_root: Path, prompt: str) -> str:
        captured_prompt.append(prompt)
        return "PROMPT.md"

    monkeypatch.setattr(commit_module, "_write_commit_prompt_file", fake_write_commit_prompt_file)
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="claude -p",
                output_flag="--output-format=stream-json",
                can_commit=True,
                json_parser=JsonParserType.CLAUDE,
                transport=AgentTransport.CLAUDE,
            )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(
        commit_module, "invoke_agent", _artifact_invoke(tmp_path, "fix: generated by agent")
    )

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert captured_prompt
    assert "ralph_submit_artifact" in captured_prompt[0]
    assert "mcp__ralph__ralph_submit_artifact" in captured_prompt[0]
    assert (
        "write the raw commit payload json to `.agent/tmp/commit_message.json`"
        in captured_prompt[0].lower()
    )


def test_generate_commit_falls_back_to_review_chain_when_commit_chain_unusable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        commit_module,
        "load_config",
        lambda *args, **kwargs: UnifiedConfig(
            agent_chains={
                "commit_chain": ["ghost-agent"],
                "review_chain": ["codex"],
            },
            agent_drains={"commit": "commit_chain", "review": "review_chain"},
        ),
    )
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    invoked_commands: list[str] = []

    def fake_invoke_agent(agent_config, *_args, **_kwargs):
        invoked_commands.append(agent_config.cmd)
        write_commit_message_artifact(tmp_path, "fix: review fallback message")
        return iter([])

    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert invoked_commands == ["codex exec"]
    output = stream.getvalue()
    assert "Generated commit message" in output
    assert "fix: review fallback message" in output


def test_generate_commit_msg_writes_commit_message_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(
        commit_module,
        "invoke_agent",
        _artifact_invoke(tmp_path, "feat: persist generated commit message"),
    )

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    artifact_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
    commit_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
    assert artifact_file.exists()
    artifact = json.loads(artifact_file.read_text(encoding="utf-8"))
    assert artifact["type"] == "commit_message"
    assert artifact["content"] == {
        "type": "commit",
        "subject": "feat: persist generated commit message",
    }
    assert commit_file.exists()
    assert commit_file.read_text(encoding="utf-8") == "feat: persist generated commit message"
    output = stream.getvalue()
    assert "Generated commit message" in output


def test_generate_commit_msg_extracts_commit_subject_from_markdown_wrapper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(
        commit_module,
        "invoke_agent",
        _artifact_invoke(tmp_path, "fix: normalize commit subject"),
    )

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    commit_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
    assert commit_file.read_text(encoding="utf-8") == "fix: normalize commit subject"
    output = stream.getvalue()
    assert "fix: normalize commit subject" in output
    assert "Generated commit message" in output


def test_generate_commit_msg_applies_sanitized_subject_when_committing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    monkeypatch.setattr(commit_module, "stage_all", lambda _root: None)
    _stub_commit_bridge(monkeypatch)

    committed_messages: list[str] = []

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(
        commit_module,
        "invoke_agent",
        _artifact_invoke(tmp_path, "feat: use extracted subject"),
    )
    monkeypatch.setattr(
        commit_module,
        "create_commit",
        lambda _root, message, **_kwargs: committed_messages.append(message) or "abc12345",
    )

    commit_module.commit_plumbing(options=commit_module.CommitPlumbingOptions(generate_commit=True))

    assert committed_messages == ["feat: use extracted subject"]
    assert "feat: use extracted subject" in stream.getvalue()


def test_generate_commit_applies_message_from_persisted_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    monkeypatch.setattr(commit_module, "stage_all", lambda _root: None)
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    committed_messages: list[str] = []

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(
        commit_module,
        "invoke_agent",
        _artifact_invoke(tmp_path, "fix: persist then commit"),
    )
    monkeypatch.setattr(
        commit_module,
        "create_commit",
        lambda _root, message, **_kwargs: committed_messages.append(message) or "abc12345",
    )

    commit_module.commit_plumbing(options=commit_module.CommitPlumbingOptions(generate_commit=True))

    artifact_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
    commit_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
    assert committed_messages == ["fix: persist then commit"]
    assert not artifact_file.exists()
    assert not commit_file.exists()
    assert "Created commit" in stream.getvalue()


def test_generate_commit_preserves_artifacts_when_commit_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    monkeypatch.setattr(commit_module, "stage_all", lambda _root: None)
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    def fail_commit(_root, _message, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(
        commit_module,
        "invoke_agent",
        _artifact_invoke(tmp_path, "fix: preserve artifacts on failure"),
    )
    monkeypatch.setattr(commit_module, "create_commit", fail_commit)

    commit_module.commit_plumbing(options=commit_module.CommitPlumbingOptions(generate_commit=True))

    artifact_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
    commit_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
    assert artifact_file.exists()
    assert commit_file.exists()
    assert "Commit failed" in stream.getvalue()


def test_show_commit_msg_reads_artifact_without_staged_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    artifact_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
    artifact_file.parent.mkdir(parents=True, exist_ok=True)
    artifact_file.write_text(
        json.dumps(
            {
                "name": "commit_message",
                "type": "commit_message",
                "content": {"message": "fix: read stored commit message"},
                "created_at": "STATIC",
                "updated_at": "STATIC",
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "has_staged_changes",
        lambda _repo_root: False,
    )

    commit_module.commit_plumbing(options=commit_module.CommitPlumbingOptions(show_commit_msg=True))

    output = stream.getvalue()
    assert "fix: read stored commit message" in output
    assert "No staged changes to commit" not in output


def test_show_commit_msg_reports_missing_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "has_staged_changes",
        lambda _repo_root: False,
    )

    commit_module.commit_plumbing(options=commit_module.CommitPlumbingOptions(show_commit_msg=True))

    assert "No commit message generated yet" in stream.getvalue()


def test_generate_commit_msg_skip_deletes_existing_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    commit_file = tmp_path / ".agent" / "tmp" / "commit-message.txt"
    commit_file.parent.mkdir(parents=True, exist_ok=True)
    commit_file.write_text("feat: old message", encoding="utf-8")

    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(
        commit_module,
        "invoke_agent",
        _artifact_invoke(tmp_path, "SKIP: No commit needed"),
    )

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert not commit_file.exists()
    assert "Skipping commit: agent requested skip" in stream.getvalue()


def test_generate_commit_msg_surfaces_parsed_agent_output_when_artifact_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    def fake_invoke_agent(*_args, **_kwargs):
        return iter(
            [
                '{"type":"response.output_text.delta","delta":"artifact write failed"}\n',
                '{"type":"error","message":"commit tool unavailable"}\n',
            ]
        )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    output = stream.getvalue()
    assert "Failed to generate commit message from commit drain agents" in output
    assert "artifact write failed" in output
    assert "commit tool unavailable" in output


def test_generate_commit_msg_surfaces_agent_invocation_error_details(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    prompt_path = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: str(prompt_path)
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="opencode",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.OPENCODE,
            )

    def fake_invoke_agent(*_args, **_kwargs):
        raise commit_module.AgentInvocationError("opencode", 1, "stderr exploded")

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    output = stream.getvalue()
    assert "Failed to generate commit message from commit drain agents" in output
    assert "stderr exploded" in output
    assert "Prompt file:" in output


def test_write_commit_prompt_file_uses_unique_path(tmp_path: Path) -> None:
    first = Path(commit_module._write_commit_prompt_file(tmp_path, "alpha"))
    second = Path(commit_module._write_commit_prompt_file(tmp_path, "beta"))

    assert first != second
    assert not first.exists()
    assert second.read_text(encoding="utf-8") == "beta"
    assert first.parent == tmp_path / ".agent" / "tmp"


def test_write_commit_prompt_file_clears_stale_commit_prompts(tmp_path: Path) -> None:
    tmp_dir = tmp_path / ".agent" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stale = tmp_dir / "commit_prompt_stale.md"
    stale.write_text("stale", encoding="utf-8")

    fresh = Path(commit_module._write_commit_prompt_file(tmp_path, "fresh"))

    assert not stale.exists()
    assert fresh.exists()
    assert fresh.read_text(encoding="utf-8") == "fresh"


def test_generate_commit_msg_preserves_streamed_output_when_agent_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    def fake_invoke_agent(*_args, **_kwargs):
        def _generator():
            yield '{"type":"response.output_text.delta","delta":"tool call started"}\n'
            yield '{"type":"error","message":"artifact submission failed"}\n'
            raise commit_module.AgentInvocationError("codex", 1, "process died after output")

        return _generator()

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    output = stream.getvalue()
    assert "tool call started" in output
    assert "artifact submission failed" in output
    assert "process died after output" in output


def test_generate_commit_msg_surfaces_structured_tool_results_when_artifact_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="codex",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    def fake_invoke_agent(*_args, **_kwargs):
        return iter(
            [
                '{"type":"item.completed","item":'
                '{"type":"mcp_tool_result","tool":"ralph_submit_artifact",'
                '"result":{"status":"failed","reason":"invalid payload"}}}\n'
            ]
        )

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    output = stream.getvalue()
    assert "ralph_submit_artifact" in output
    assert 'result={"reason": "invalid' in output
    assert 'payload", "status": "failed"}' in output


def test_generate_commit_msg_accepts_raw_commit_payload_written_by_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "_working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "_write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config):
            return cls()

        def get(self, _name: str):
            return AgentConfig(
                cmd="claude -p",
                output_flag="--output-format=stream-json",
                can_commit=True,
                json_parser=JsonParserType.CLAUDE,
                transport=AgentTransport.CLAUDE,
            )

    def fake_invoke_agent(*_args, **_kwargs):
        artifact_path = tmp_path / ".agent" / "tmp" / "commit_message.json"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            json.dumps(
                {
                    "type": "commit",
                    "subject": "fix(cli): salvage commit fallback",
                }
            ),
            encoding="utf-8",
        )
        line = json.dumps(
            {
                "type": "response.output_text.delta",
                "delta": "tool unavailable; wrote raw payload",
            }
        )
        return iter([f"{line}\n"])

    monkeypatch.setattr(commit_module, "AgentRegistry", FakeRegistry)
    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    output = stream.getvalue()
    assert "Generated commit message" in output
    assert "fix(cli): salvage commit fallback" in output


def test_handle_show_or_generate_displays_staged_files(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    files = [f"file_{i}" for i in range(commit_module._MAX_DISPLAY_FILES + 2)]
    monkeypatch.setattr(commit_module, "get_staged_files", lambda root: files)
    monkeypatch.setattr(commit_module, "_generate_commit_message", lambda _staged, root: "auto msg")

    def fail_commit(*args: object, **kwargs: object) -> Path:
        raise AssertionError("Should not commit when apply=False")

    monkeypatch.setattr(commit_module, "create_commit", fail_commit)

    commit_module._handle_show_or_generate(
        repo_root=Path("/tmp"),
        generate=True,
        apply=False,
        git_user_name="user",
        git_user_email="user@example.com",
    )

    output = stream.getvalue()
    assert "Staged files" in output
    assert "... and 2 more" in output
    assert "auto msg" in output
    assert "Generated commit message" in output


def test_handle_show_or_generate_applies_commit_success(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "get_staged_files", lambda root: ["src/app.py"])
    monkeypatch.setattr(commit_module, "_generate_commit_message", lambda _staged, root: "auto msg")
    recorded: list[str] = []

    def fake_create(
        repo_root: Path, message: str, author_name: str | None, author_email: str | None
    ) -> str:
        recorded.append(repo_root.as_posix())
        return "deadbeef1234"

    monkeypatch.setattr(commit_module, "create_commit", fake_create)
    commit_module._handle_show_or_generate(
        repo_root=Path("/tmp"),
        generate=True,
        apply=True,
        git_user_name="user",
        git_user_email="user@example.com",
    )

    assert recorded
    output = stream.getvalue()
    assert "Created commit" in output
    assert "deadbeef" in output


def test_handle_show_or_generate_applies_commit_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "get_staged_files", lambda root: ["src/app.py"])
    monkeypatch.setattr(commit_module, "_generate_commit_message", lambda _staged, root: "auto msg")

    def raise_commit(*args: object, **kwargs: object) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(commit_module, "create_commit", raise_commit)
    commit_module._handle_show_or_generate(
        repo_root=Path("/tmp"),
        generate=True,
        apply=True,
        git_user_name="user",
        git_user_email="user@example.com",
    )

    assert "Commit failed" in stream.getvalue()


def test_generate_commit_message_synthesizes_sections() -> None:
    assert commit_module._generate_commit_message([], Path("/tmp")) == "Update files"
    message = commit_module._generate_commit_message(
        ["src/one.py", "tests/two.py", "docs/three.md"], Path("/tmp")
    )
    assert "Update 2 files" in message
    assert "Modify 1 file" in message


def test_check_git_repo_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)

    def raise_repo() -> Path:
        raise RuntimeError("missing")

    monkeypatch.setattr(diagnose_module, "find_repo_root", raise_repo)
    diagnose_module._check_git_repo()
    assert "Git Repository" in stream.getvalue()
    assert "Error" in stream.getvalue()


def test_check_git_repo_clean_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    monkeypatch.setattr(diagnose_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(diagnose_module, "is_repo_clean", lambda root: True)

    diagnose_module._check_git_repo()
    output = stream.getvalue()
    assert "Working tree" in output
    assert "Clean" in output


def test_check_configuration_success(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    config = SimpleNamespace(
        general=SimpleNamespace(
            developer_iters=4,
            reviewer_reviews=2,
            review_depth=ReviewDepth.SECURITY,
            workflow=SimpleNamespace(checkpoint_enabled=False),
        )
    )
    monkeypatch.setattr(diagnose_module, "load_config", lambda *args, **kwargs: config)
    diagnose_module._check_configuration(None, {})
    output = stream.getvalue()
    assert "Config loaded" in output
    assert "Developer iters" in output


def test_check_configuration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)

    def raise_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(diagnose_module, "load_config", raise_config)
    diagnose_module._check_configuration(None, {})
    assert "Error" in stream.getvalue()


def test_check_agents_no_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    monkeypatch.setattr(
        diagnose_module, "load_config", lambda *args, **kwargs: SimpleNamespace(agents={})
    )
    diagnose_module._check_agents({})
    assert "No agents configured" in stream.getvalue()


def test_check_agents_with_configured_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    agent = AgentConfig(cmd="agent", can_commit=True)
    monkeypatch.setattr(
        diagnose_module,
        "load_config",
        lambda *args, **kwargs: SimpleNamespace(agents={"alpha": agent}),
    )
    diagnose_module._check_agents({})
    output = stream.getvalue()
    assert "Configured" in output
    assert "alpha" in output


def test_check_agents_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)

    def raise_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(diagnose_module, "load_config", raise_config)
    diagnose_module._check_agents({})
    assert "Error" in stream.getvalue()


def test_check_workspace_files_reports_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, diagnose_module)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "PROMPT.md").write_text("prompt")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text("config")

    diagnose_module._check_workspace_files()
    output = stream.getvalue()
    assert "PROMPT.md" in output
    assert "Exists" in output
    assert "checkpoint" in output.lower()
    assert "Not found" in output


def test_init_command_creates_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)
    init_module.init_command(template="starter-template")
    assert (tmp_path / "PROMPT.md").exists()
    assert (tmp_path / ".agent" / "ralph-workflow.toml").exists()
    output = stream.getvalue()
    assert "Ralph" in output
    assert "Created" in output


def test_init_command_keeps_existing_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("existing")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    config = agent_dir / "ralph-workflow.toml"
    config.write_text("existing config")

    init_module.init_command(template="starter-template")
    assert prompt.read_text() == "existing"
    assert config.read_text() == "existing config"
    assert "Created" not in stream.getvalue()


def test_init_command_custom_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "custom" / "custom.toml"
    custom.parent.mkdir()
    init_module.init_command(template="starter-template", config_path=custom)
    assert custom.exists()
    assert "Created" in stream.getvalue()


def test_init_command_creates_prompt_in_cwd_not_template_subdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)
    init_module.init_command(template="starter-template")
    assert (tmp_path / "PROMPT.md").exists()
    assert not (tmp_path / "starter-template").exists()


def test_init_command_creates_agent_dir_in_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)
    init_module.init_command(template="starter-template")
    assert (tmp_path / ".agent").is_dir()
    assert (tmp_path / ".agent" / "ralph-workflow.toml").exists()


def test_init_command_default_template(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch, init_module)
    monkeypatch.chdir(tmp_path)
    init_module.init_command()
    assert (tmp_path / "PROMPT.md").exists()
    output = stream.getvalue()
    assert "default" in output


def test_verbosity_option_processes_values() -> None:
    ctx = click.Context(click.Command("test"))
    option = options_module.VerbosityOption(param_decls=["--verbosity"])
    # Default (None) now maps to VERBOSE: Ralph is verbose by default.
    assert option.process_value(ctx, None) == Verbosity.VERBOSE
    assert option.process_value(ctx, Verbosity.FULL) == Verbosity.FULL
    assert option.process_value(ctx, "debug") == Verbosity.DEBUG
    assert option.process_value(ctx, "3") == Verbosity.FULL
    assert option.process_value(ctx, "20") == Verbosity.DEBUG
    assert option.process_value(ctx, "nonsense") == Verbosity.VERBOSE


def test_display_tables_render() -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None)
    agent = AgentConfig(cmd="agent", can_commit=False)
    options_module.display_agents_table({"alpha": agent}, console=console)
    rendered = buffer.getvalue()
    assert "Configured" in rendered
    assert "Agents" in rendered
    assert "alpha" in rendered
    assert "no" in rendered

    buffer.truncate(0)
    buffer.seek(0)
    options_module.display_providers_table(["opencode"], console=console)
    rendered = buffer.getvalue()
    assert "Available" in rendered
    assert "Providers" in rendered
    assert "opencode" in rendered
