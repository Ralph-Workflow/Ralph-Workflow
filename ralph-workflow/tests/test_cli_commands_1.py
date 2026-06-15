"""Focused CLI command tests for commit, diagnose, init, and option helpers."""

from __future__ import annotations

import json
import sys
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

from git import Repo
from rich.console import Console

from ralph.cli.commands import commit as commit_module
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, GeneralConfig, UnifiedConfig
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME
from ralph.mcp.artifacts.commit_message import write_commit_message_artifact
from ralph.mcp.protocol.env import AGENT_LABEL_SCOPE_ENV, MCP_ENDPOINT_ENV, MCP_RUN_ID_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.session_plan import build_session_mcp_plan
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.scope import WorkspaceScope

if TYPE_CHECKING:
    import pytest


_SUMMARY_RETRY_FAILURES = 2


def _artifact_invoke(tmp_path: Path, message: str) -> object:
    def _fake(agent: object, prompt_file: str, *, options: object = None) -> object:
        write_commit_message_artifact(tmp_path, message)
        return iter([])

    return _fake


def _attach_console(monkeypatch: pytest.MonkeyPatch, module: object) -> StringIO:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)

    ctx = DisplayContext(
        console=console,
        theme=RALPH_THEME,
        width=80,
        mode="wide",
        narrow=False,
        color_enabled=True,
        glyphs_enabled=True,
        headline_max_chars=120,
        condenser_soft_limit=400,
        condenser_hard_limit=4000,
        streaming_checkpoint_chars=4000,
        streaming_checkpoint_fragments=20,
        streaming_dedup_enabled=True,
        streaming_checkpoints_enabled=True,
        thinking_preview_min_chars=80,
        tool_result_headline_min_chars=80,
    )

    def fake_make_display_context(**kwargs: object) -> object:
        return ctx

    monkeypatch.setattr(module, "make_display_context", fake_make_display_context)
    return stream


def _simple_config() -> SimpleNamespace:
    return SimpleNamespace(
        general=GeneralConfig(
            git_user_name="user",
            git_user_email="user@example.com",
            verbosity=2,
        ),
        agent_drains={
            "commit": AgentDrainConfig(chain="commit_chain"),
            "review": AgentDrainConfig(chain="review_chain"),
        },
        agent_chains={
            "commit_chain": AgentChainConfig(agents=["commit_agent"]),
            "review_chain": AgentChainConfig(agents=["review_agent"]),
        },
    )


def _stub_commit_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBridge:
        @property
        def run_id(self) -> str:
            return "fake-run-id"

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(commit_module, "start_commit_bridge", lambda _repo_root: FakeBridge())


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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, _name: str) -> object:
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


def test_working_tree_diff_excludes_mid_cycle_committed_files(tmp_git_repo: Path) -> None:
    """_working_tree_diff must exclude files committed in earlier mid-cycle commits.

    The standalone commit plumbing must use HEAD-only diff semantics so that
    files committed during an earlier dev iteration do not appear in the prompt
    sent to the commit agent.
    """

    with Repo(tmp_git_repo) as repo:
        (tmp_git_repo / "mid_cycle.py").write_text("mid = 1\n")
        repo.index.add(["mid_cycle.py"])
        repo.index.commit("mid-cycle commit")
        (tmp_git_repo / "pending.py").write_text("pending = 2\n")
        repo.index.add(["pending.py"])

    diff = commit_module.working_tree_diff(tmp_git_repo)

    assert "pending.py" in diff
    assert "mid_cycle.py" not in diff


def test_commit_bridge_session_plan_grants_write_ephemeral(tmp_path: Path) -> None:
    """The MCP session built for commit plumbing must expose workspace.write_ephemeral.

    Commit prompts instruct the agent to fall back to write_file when
    artifact.submit is unavailable. That fallback only works if the commit
    session grants workspace.write_ephemeral so the MCP server allows the
    write_file tool call to .agent/tmp/commit_message.json.
    """
    agents_policy = AgentsPolicy(
        agent_chains={"commit_chain": AgentChainConfig(agents=["claude"])},
        agent_drains={"commit": AgentDrainConfig(chain="commit_chain", drain_class="commit")},
    )
    plan = build_session_mcp_plan(
        transport=None,
        drain="commit",
        workspace_path=tmp_path,
        agents_policy=agents_policy,
    )

    assert "workspace.write_ephemeral" in plan.capabilities
    assert "workspace.write_tracked" not in plan.capabilities
    assert "git.write" not in plan.capabilities


def test_dead_cli_option_helpers_are_not_exposed_by_options_module() -> None:
    """Cleanup should remove the unused option helper decorators entirely.

    After wt-007, ralph.cli.options was deleted. The cleanup contract is
    now: those dead helpers simply do not exist anywhere on the public
    CLI surface.
    """
    cli_pkg = sys.modules.get("ralph.cli")
    options_module = getattr(cli_pkg, "options", None) if cli_pkg is not None else None
    if options_module is None:
        return
    assert not hasattr(options_module, "verbose_option")
    assert not hasattr(options_module, "quiet_option")
    assert not hasattr(options_module, "config_option")


def test_generate_commit_uses_commit_drain_agent_chain(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    simple_config = _simple_config()
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: simple_config)
    _stub_commit_bridge(monkeypatch)
    monkeypatch.setattr(
        commit_module,
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )

    invoked_agents: list[str] = []

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, name: str) -> object:
            cmd = "codex" if name == "commit_agent" else "claude -p"
            return AgentConfig(
                cmd=cmd,
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.CODEX,
            )

    def fake_invoke_agent(agent_config: object, *_args: object, **_kwargs: object) -> object:
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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)
    monkeypatch.setattr(commit_module, "validate_local_model_support", lambda *args, **kwargs: None)

    invoked_model_flags: list[str | None] = []

    def fake_invoke_agent(agent_config: object, *_args: object, **_kwargs: object) -> object:
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
        "working_tree_diff",
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
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, _name: str) -> object:
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

    def fake_invoke_agent(_agent_config: object, *_args: object, **kwargs: object) -> object:
        options = kwargs.get("options")
        seen_session_ids.append(None if options is None else options.session_id)
        if len(seen_session_ids) == 1:
            return iter(['{"type":"session","session_id":"claude-session-1"}'])
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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    _stub_commit_bridge(monkeypatch)
    monkeypatch.setattr(commit_module, "validate_local_model_support", lambda *args, **kwargs: None)

    prompt_bodies: list[str] = []

    def fake_write_commit_prompt_file(_root: Path, prompt: str) -> str:
        prompt_bodies.append(prompt)
        prompt_file = tmp_path / f"prompt-{len(prompt_bodies)}.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        return str(prompt_file)

    monkeypatch.setattr(commit_module, "write_commit_prompt_file", fake_write_commit_prompt_file)

    invoked_agents: list[tuple[str, str | None]] = []

    def fake_invoke_agent(
        agent_config: object, prompt_file: object, *_args: object, **kwargs: object
    ) -> object:
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
        ("claude", None),
        ("claude", None),
        ("opencode", None),
    ]
    # The retry guidance is the shared build_retry_hint output (same as the
    # pipeline phase gates), so the assertions pin the unified hint, not a
    # commit-specific prompt.
    assert any("required artifact 'commit_message'" in body for body in prompt_bodies[1:])
    assert any('artifact_type="commit_message"' in body for body in prompt_bodies[1:])
    assert any(".agent/tmp/commit_message.json" in body for body in prompt_bodies[1:])
    assert any("Do not use content_path for this retry" in body for body in prompt_bodies[1:])
    assert any("Submit the artifact now" in body for body in prompt_bodies[1:])
    output = stream.getvalue()
    assert "Generated commit message" in output
    assert "fix: fallback agent message" in output


def test_generate_commit_skips_locally_unsupported_opencode_commit_agents(
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
                "commit_chain": [
                    "opencode/minimax/MiniMax-M2.7-highspeed",
                    "opencode/kimi-for-coding/k2p6",
                ]
            },
            agent_drains={"commit": "commit_chain", "review": "commit_chain"},
        ),
    )
    monkeypatch.setattr(
        commit_module,
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    monkeypatch.setattr(
        commit_module,
        "validate_local_model_support",
        lambda model_id, **kwargs: "provider unsupported"
        if model_id == "minimax/MiniMax-M2.7-highspeed"
        else None,
    )

    invoked_model_flags: list[str | None] = []

    def fake_invoke_agent(agent_config: object, *_args: object, **_kwargs: object) -> object:
        invoked_model_flags.append(agent_config.model_flag)
        write_commit_message_artifact(tmp_path, "fix: commit drain message")
        return iter([])

    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert invoked_model_flags == ["-m kimi-for-coding/k2p6"]
    output = stream.getvalue()
    assert "Generated commit message" in output
    assert "fix: commit drain message" in output


def test_generate_commit_passes_mcp_endpoint_to_opencode_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, _name: str) -> object:
            return AgentConfig(
                cmd="opencode",
                output_flag="--json-stream",
                can_commit=True,
                json_parser=JsonParserType.OPENCODE,
            )

    seen_extra_env: list[dict[str, str] | None] = []

    def fake_invoke_agent(_agent_config: object, *_args: object, **kwargs: object) -> object:
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
            str(MCP_ENDPOINT_ENV): "http://127.0.0.1:9999/mcp",
            str(MCP_RUN_ID_ENV): "commit-plumbing",
            str(AGENT_LABEL_SCOPE_ENV): "commit-plumbing",
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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    captured_prompt: list[str] = []

    def fake_write_commit_prompt_file(_root: Path, prompt: str) -> str:
        captured_prompt.append(prompt)
        return "PROMPT.md"

    monkeypatch.setattr(commit_module, "write_commit_prompt_file", fake_write_commit_prompt_file)
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, _name: str) -> object:
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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    captured_prompt: list[str] = []

    def fake_write_commit_prompt_file(_root: Path, prompt: str) -> str:
        captured_prompt.append(prompt)
        return "PROMPT.md"

    monkeypatch.setattr(commit_module, "write_commit_prompt_file", fake_write_commit_prompt_file)
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, _name: str) -> object:
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
    # Architectural fix (2026-06-14): the template's REQUIRED PROCEDURE
    # section (which carried the original "write the raw commit payload"
    # text) was removed. The shared artifact submission macro is now
    # the single source of truth for the unavailable-tool fallback;
    # it uses the wording "raw inner payload" rather than "raw commit
    # payload". Both surfaces point at the same
    # ``.agent/tmp/commit_message.json`` path, so the test must assert
    # against the macro's wording, not the (now-removed) duplicate
    # procedure's wording. The macro's step 6 wraps onto two lines in
    # the rendered output; match the substrings separately to avoid
    # line-break brittleness.
    assert "raw inner" in captured_prompt[0].lower()
    assert "payload json" in captured_prompt[0].lower()
    assert ".agent/tmp/commit_message.json" in captured_prompt[0]


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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    invoked_commands: list[str] = []

    def fake_invoke_agent(agent_config: object, *_args: object, **_kwargs: object) -> object:
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


def test_generate_commit_appends_default_fallback_after_configured_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        commit_module,
        "load_config",
        lambda *args, **kwargs: UnifiedConfig(
            agent_chains={"commit_chain": ["opencode/kimi-for-coding/k2p6"]},
            agent_drains={"commit": "commit_chain", "review": "commit_chain"},
        ),
    )
    monkeypatch.setattr(
        commit_module,
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)
    monkeypatch.setattr(commit_module, "validate_local_model_support", lambda *args, **kwargs: None)

    invoked_commands: list[str] = []

    def fake_invoke_agent(agent_config: object, *_args: object, **_kwargs: object) -> object:
        invoked_commands.append(agent_config.cmd)
        if agent_config.cmd == "claude":
            write_commit_message_artifact(tmp_path, "fix: default fallback message")
            return iter([])
        if len(invoked_commands) <= 2:
            return iter([])
        return iter([])

    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    assert invoked_commands == ["opencode", "opencode", "claude"]
    output = stream.getvalue()
    assert "Generated commit message" in output
    assert "fix: default fallback message" in output


def test_generate_commit_msg_writes_commit_message_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = _attach_console(monkeypatch, commit_module)
    monkeypatch.setattr(commit_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(commit_module, "load_config", lambda *args, **kwargs: _simple_config())
    monkeypatch.setattr(
        commit_module,
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, _name: str) -> object:
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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, _name: str) -> object:
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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    monkeypatch.setattr(commit_module, "stage_all", lambda _root: None)
    _stub_commit_bridge(monkeypatch)

    committed_messages: list[str] = []

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, _name: str) -> object:
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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: "PROMPT.md"
    )
    monkeypatch.setattr(commit_module, "stage_all", lambda _root: None)
    _stub_commit_bridge(monkeypatch)

    class FakeRegistry:
        @classmethod
        def from_config(cls, _config: object) -> object:
            return cls()

        def get(self, _name: str) -> object:
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
