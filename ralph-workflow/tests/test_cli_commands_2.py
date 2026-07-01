"""Focused CLI command tests for commit, diagnose, init, and option helpers.

These tests are subprocess_e2e: they exercise the real CLI entry points
and their full filesystem path. They cannot be mocked down to the
per-test 1 s budget without losing the end-to-end contract they assert.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
import tomllib
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console

from ralph.cli.commands import commit as commit_module
from ralph.cli.commands import diagnose as diagnose_module
from ralph.cli.commands import init as init_module
from ralph.cli.commands.check_policy import check_policy_command
from ralph.config.enums import AgentTransport, JsonParserType
from ralph.config.models import AgentConfig, GeneralConfig
from ralph.display.context import DisplayContext, make_display_context
from ralph.display.parallel_display import ParallelDisplay
from ralph.display.theme import RALPH_THEME
from ralph.mcp.artifacts.commit_message import write_commit_message_artifact
from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    resolve_capability_profile,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.policy.loader import default_dir as _policy_default_dir
from ralph.policy.models import AgentChainConfig, AgentDrainConfig
from ralph.skills._capability_state import CapabilityState


def _stub_baseline_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace init_command's baseline-skill-install path with a fast no-op.

    The default branch of ``init_command`` invokes
    ``_ensure_baseline_capabilities`` which constructs a real
    ``SkillManager``, walks every registered agent root, copies bundled
    skill files into the user-global directory, and prints the full
    capability summary. For unit tests that only assert on file creation
    or pre-existing-file preservation this is overkill — the public
    behaviour covered here is the bootstrap files (``PROMPT.md`` and the
    per-support-config toml files), not the skill installer. Replacing
    the private helper with a no-op returning ``(CapabilityState(), [])``
    skips the slow real-IO work without changing what these tests pin.
    """
    monkeypatch.setattr(
        init_module,
        "_ensure_baseline_capabilities",
        lambda *, display_context: (CapabilityState(), []),
    )


pytestmark = [pytest.mark.timeout_seconds(10), pytest.mark.subprocess_e2e]


_SUMMARY_RETRY_FAILURES = 2
_POLICY_VALIDATION_EXIT_CODE = 2


def _artifact_invoke(tmp_path: Path, message: str) -> object:
    def _fake(agent: object, prompt_file: str, *, options: object = None) -> object:
        write_commit_message_artifact(tmp_path, message)
        return iter([])

    return _fake


def _copy_defaults(tmp_path: Path) -> None:
    defaults = _policy_default_dir()
    for name in ("pipeline.toml", "agents.toml", "artifacts.toml"):
        shutil.copy(defaults / name, tmp_path / name)


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


def test_generate_commit_preserves_artifacts_when_commit_fails(
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

    def fail_commit(_root: object, _message: object, **_kwargs: object) -> None:
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

    def fake_invoke_agent(*_args: object, **_kwargs: object) -> object:
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
        "working_tree_diff",
        lambda _root: "diff --git a/src/app.py b/src/app.py\n+print('hi')",
    )
    prompt_path = tmp_path / ".agent" / "tmp" / "commit_prompt.md"
    monkeypatch.setattr(
        commit_module, "write_commit_prompt_file", lambda _root, _prompt: str(prompt_path)
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

    def fake_invoke_agent(*_args: object, **_kwargs: object) -> None:
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
    first = Path(commit_module.write_commit_prompt_file(tmp_path, "alpha"))
    second = Path(commit_module.write_commit_prompt_file(tmp_path, "beta"))

    assert first != second
    assert not first.exists()
    assert second.read_text(encoding="utf-8") == "beta"
    assert first.parent == tmp_path / ".agent" / "tmp"


def test_write_commit_prompt_file_clears_stale_commit_prompts(tmp_path: Path) -> None:
    tmp_dir = tmp_path / ".agent" / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stale = tmp_dir / "commit_prompt_stale.md"
    stale.write_text("stale", encoding="utf-8")

    fresh = Path(commit_module.write_commit_prompt_file(tmp_path, "fresh"))

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

    def fake_invoke_agent(*_args: object, **_kwargs: object) -> object:
        def _generator() -> object:
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

    def fake_invoke_agent(*_args: object, **_kwargs: object) -> object:
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
                cmd="claude -p",
                output_flag="--output-format=stream-json",
                can_commit=True,
                json_parser=JsonParserType.CLAUDE,
                transport=AgentTransport.CLAUDE,
            )

    def fake_invoke_agent(*_args: object, **_kwargs: object) -> object:
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


def test_check_git_repo_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console, env={})

    def raise_repo() -> Path:
        raise RuntimeError("missing")

    monkeypatch.setattr(diagnose_module, "find_repo_root", raise_repo)
    diagnose_module.check_git_repo(display_context=ctx)
    assert "Git Repository" in stream.getvalue()
    assert "Error" in stream.getvalue()


def test_check_git_repo_clean_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console, env={})
    monkeypatch.setattr(diagnose_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(diagnose_module, "is_repo_clean", lambda root: True)

    diagnose_module.check_git_repo(display_context=ctx)
    output = stream.getvalue()
    assert "Working tree" in output
    assert "Clean" in output


def test_check_configuration_success(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console, env={})
    config = SimpleNamespace(
        general=SimpleNamespace(
            developer_iters=5,
            workflow=SimpleNamespace(checkpoint_enabled=False),
        )
    )
    monkeypatch.setattr(diagnose_module, "load_config", lambda *args, **kwargs: config)
    diagnose_module.check_configuration(None, {}, display_context=ctx)
    output = stream.getvalue()
    assert "Config loaded" in output
    assert "Developer iters" in output
    assert "Checkpoint enabled" in output


def test_check_configuration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console, env={})

    def raise_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(diagnose_module, "load_config", raise_config)
    diagnose_module.check_configuration(None, {}, display_context=ctx)
    assert "Error" in stream.getvalue()


def test_check_agents_no_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console, env={})
    fake_registry = SimpleNamespace(list_agents=lambda: [])
    monkeypatch.setattr(diagnose_module, "load_config", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        diagnose_module, "AgentRegistry", SimpleNamespace(from_config=lambda c: fake_registry)
    )
    diagnose_module.check_agents({}, display_context=ctx)
    assert "No agents configured" in stream.getvalue()


def test_check_agents_with_configured_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console, env={})
    agent = AgentConfig(cmd="agent", can_commit=True)
    fake_registry = SimpleNamespace(
        list_agents=lambda: ["alpha"],
        get=lambda name: agent,
    )
    monkeypatch.setattr(diagnose_module, "load_config", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(
        diagnose_module, "AgentRegistry", SimpleNamespace(from_config=lambda c: fake_registry)
    )
    monkeypatch.setattr(
        diagnose_module, "check_agent_availability", lambda r: [("alpha", "available")]
    )
    diagnose_module.check_agents({}, display_context=ctx)
    output = stream.getvalue()
    assert "Configured" in output
    assert "alpha" in output


def test_check_agents_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console, env={})

    def raise_config(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(diagnose_module, "load_config", raise_config)
    diagnose_module.check_agents({}, display_context=ctx)
    assert "Error" in stream.getvalue()


def test_check_workspace_files_reports_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console, env={})
    monkeypatch.chdir(tmp_path)
    (tmp_path / "PROMPT.md").write_text("prompt")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text("config")

    diagnose_module.check_workspace_files(display_context=ctx)
    output = stream.getvalue()
    assert "PROMPT.md" in output
    assert "Exists" in output
    assert "checkpoint" in output.lower()
    assert "Not found" in output


def test_diagnose_uses_display_context_console(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """diagnose_command uses the injected DisplayContext's console for all output."""

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(env={"COLUMNS": "120"})
    # Override the context's console with our recording one

    recording_ctx = dataclasses.replace(ctx, console=console)

    # Stub out external dependencies so the command finishes quickly
    monkeypatch.setattr(diagnose_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(diagnose_module, "is_repo_clean", lambda root: True)
    monkeypatch.setattr(
        diagnose_module,
        "resolve_workspace_scope",
        lambda: __import__("ralph.workspace.scope", fromlist=["WorkspaceScope"]).WorkspaceScope(
            tmp_path
        ),
    )
    monkeypatch.setattr(
        diagnose_module,
        "load_config",
        lambda *a, **kw: SimpleNamespace(
            general=SimpleNamespace(
                developer_iters=5,
                workflow=SimpleNamespace(checkpoint_enabled=True),
            )
        ),
    )
    monkeypatch.setattr(
        diagnose_module,
        "AgentRegistry",
        SimpleNamespace(from_config=lambda c: SimpleNamespace(list_agents=lambda: [])),
    )

    diagnose_module.diagnose_command(display_context=recording_ctx)

    output = stream.getvalue()
    assert "Diagnostics" in output
    assert "Git Repository" in output


def test_diagnose_command_displays_capability_state_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """diagnose_command should display a Baseline Capabilities table."""

    stream = StringIO()
    console = Console(file=stream, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(env={"COLUMNS": "120"})
    recording_ctx = dataclasses.replace(ctx, console=console)

    monkeypatch.setattr(diagnose_module, "find_repo_root", lambda: tmp_path)
    monkeypatch.setattr(diagnose_module, "is_repo_clean", lambda root: True)
    monkeypatch.setattr(
        diagnose_module,
        "resolve_workspace_scope",
        lambda: __import__("ralph.workspace.scope", fromlist=["WorkspaceScope"]).WorkspaceScope(
            tmp_path
        ),
    )
    monkeypatch.setattr(
        diagnose_module,
        "load_config",
        lambda *a, **kw: SimpleNamespace(
            general=SimpleNamespace(
                developer_iters=5,
                workflow=SimpleNamespace(checkpoint_enabled=True),
            )
        ),
    )
    monkeypatch.setattr(
        diagnose_module,
        "AgentRegistry",
        SimpleNamespace(from_config=lambda c: SimpleNamespace(list_agents=lambda: [])),
    )

    diagnose_module.diagnose_command(display_context=recording_ctx)

    output = stream.getvalue()
    assert "Baseline Capabilities" in output
    # Should show Built-in capabilities (always available)
    assert "Built-in" in output
    # Should show Managed capabilities
    assert "Managed" in output


def test_init_command_creates_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, init_module)
    _stub_baseline_capabilities(monkeypatch)
    monkeypatch.chdir(tmp_path)
    init_module.init_command(template="default")
    assert (tmp_path / "PROMPT.md").exists()
    assert not (tmp_path / ".agent" / "ralph-workflow.toml").exists()
    assert (tmp_path / ".agent" / "mcp.toml").exists()
    assert (tmp_path / ".agent" / "pipeline.toml").exists()
    assert (tmp_path / ".agent" / "artifacts.toml").exists()
    output = stream.getvalue()
    assert "Ralph" in output
    assert "Created" in output


@pytest.mark.timeout_seconds(3)
def test_init_command_keeps_existing_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``init`` short-circuits when every config file already exists.

    On a loaded worker the ``init`` short-circuit checks can take ~1.1 s (the
    capability refresh and skill installer both touch disk), so a 3 s per-test
    cap is required to keep this from tripping the 1 s default and stalling
    the xdist scheduler.
    """
    xdg_dir = tmp_path / "xdg"
    xdg_dir.mkdir()
    (xdg_dir / "ralph-workflow.toml").write_text("# global", encoding="utf-8")
    (xdg_dir / "ralph-workflow-mcp.toml").write_text("# mcp", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
    stream = _attach_console(monkeypatch, init_module)
    _stub_baseline_capabilities(monkeypatch)
    monkeypatch.chdir(tmp_path)
    prompt = tmp_path / "PROMPT.md"
    prompt.write_text("existing")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    config = agent_dir / "ralph-workflow.toml"
    config.write_text("existing config")
    (agent_dir / "mcp.toml").write_text("# local mcp", encoding="utf-8")
    (agent_dir / "agents.toml").write_text("# agents", encoding="utf-8")
    (agent_dir / "pipeline.toml").write_text("# pipeline", encoding="utf-8")
    (agent_dir / "artifacts.toml").write_text("# artifacts", encoding="utf-8")

    init_module.init_command(template="default")
    assert prompt.read_text() == "existing"
    assert config.read_text() == "existing config"
    assert "Created" not in stream.getvalue()


def test_init_command_custom_config_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    stream = _attach_console(monkeypatch, init_module)
    _stub_baseline_capabilities(monkeypatch)
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "custom" / "custom.toml"
    custom.parent.mkdir()
    init_module.init_command(template="default", config_path=custom)
    assert custom.exists()
    assert "Created" in stream.getvalue()


def test_init_command_creates_prompt_in_cwd_not_template_subdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _attach_console(monkeypatch, init_module)
    _stub_baseline_capabilities(monkeypatch)
    monkeypatch.chdir(tmp_path)
    init_module.init_command(template="default")
    assert (tmp_path / "PROMPT.md").exists()
    assert not (tmp_path / "default").exists()


def test_init_command_creates_agent_dir_in_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _attach_console(monkeypatch, init_module)
    _stub_baseline_capabilities(monkeypatch)
    monkeypatch.chdir(tmp_path)
    init_module.init_command(template="default")
    assert (tmp_path / ".agent").is_dir()
    assert (tmp_path / ".agent" / "mcp.toml").exists()
    assert (tmp_path / ".agent" / "pipeline.toml").exists()
    assert (tmp_path / ".agent" / "artifacts.toml").exists()
    assert not (tmp_path / ".agent" / "ralph-workflow.toml").exists()


def test_init_command_fallback_next_steps_do_not_advertise_template_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    xdg_dir = tmp_path / "xdg"
    xdg_dir.mkdir()
    (xdg_dir / "ralph-workflow.toml").write_text("# global", encoding="utf-8")
    (xdg_dir / "ralph-workflow-mcp.toml").write_text("# mcp", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
    stream = _attach_console(monkeypatch, init_module)
    _stub_baseline_capabilities(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "PROMPT.md").write_text("existing")
    agent_dir = tmp_path / ".agent"
    agent_dir.mkdir()
    (agent_dir / "ralph-workflow.toml").write_text("existing config")
    (agent_dir / "mcp.toml").write_text("# local mcp", encoding="utf-8")
    (agent_dir / "agents.toml").write_text("# agents", encoding="utf-8")
    (agent_dir / "pipeline.toml").write_text("# pipeline", encoding="utf-8")
    (agent_dir / "artifacts.toml").write_text("# artifacts", encoding="utf-8")

    init_module.init_command()

    output = stream.getvalue()
    assert "Template:" not in output


def test_display_tables_render() -> None:
    buffer = StringIO()
    console = Console(file=buffer, force_terminal=False, color_system=None, theme=RALPH_THEME)
    ctx = make_display_context(console=console, env={})
    agent = AgentConfig(cmd="agent", can_commit=False)
    pd = ParallelDisplay(ctx)
    pd.emit_agents_table({"alpha": agent})
    rendered = buffer.getvalue()
    assert "Configured" in rendered
    assert "Agents" in rendered
    assert "alpha" in rendered
    assert "no" in rendered

    buffer.truncate(0)
    buffer.seek(0)
    pd.emit_providers_table(["opencode"])
    rendered = buffer.getvalue()
    assert "Available" in rendered
    assert "Providers" in rendered
    assert "opencode" in rendered


@pytest.mark.timeout_seconds(3)
def test_init_command_writes_support_and_global_configs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg_dir = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
    _attach_console(monkeypatch, init_module)
    _stub_baseline_capabilities(monkeypatch)
    monkeypatch.chdir(tmp_path)

    init_module.init_command(None, None)

    assert not (tmp_path / ".agent" / "ralph-workflow.toml").exists()
    assert (tmp_path / ".agent" / "mcp.toml").exists()
    assert (tmp_path / ".agent" / "pipeline.toml").exists()
    assert (tmp_path / ".agent" / "artifacts.toml").exists()
    assert (xdg_dir / "ralph-workflow.toml").exists()
    assert (xdg_dir / "ralph-workflow-mcp.toml").exists()

    assert isinstance(tomllib.loads((tmp_path / ".agent" / "mcp.toml").read_text()), dict)
    assert isinstance(tomllib.loads((tmp_path / ".agent" / "pipeline.toml").read_text()), dict)
    assert isinstance(tomllib.loads((tmp_path / ".agent" / "artifacts.toml").read_text()), dict)
    assert isinstance(tomllib.loads((xdg_dir / "ralph-workflow.toml").read_text()), dict)
    assert isinstance(tomllib.loads((xdg_dir / "ralph-workflow-mcp.toml").read_text()), dict)


def test_init_command_respects_explicit_config_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg_dir = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
    _attach_console(monkeypatch, init_module)
    _stub_baseline_capabilities(monkeypatch)
    monkeypatch.chdir(tmp_path)
    custom = tmp_path / "custom.toml"

    init_module.init_command(None, custom)

    assert custom.exists()
    assert isinstance(tomllib.loads(custom.read_text()), dict)


class TestCheckPolicyCommand:
    """check_policy_command validates the active policy and reports results."""

    def test_success_returns_zero_and_prints_ok(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _copy_defaults(tmp_path)
        code = check_policy_command(tmp_path)
        out = capsys.readouterr().out
        assert code == 0
        assert "Policy OK" in out
        assert "phases:" in out
        assert "drains:" in out
        assert "artifact contracts:" in out

    def test_success_includes_counts(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _copy_defaults(tmp_path)
        check_policy_command(tmp_path)
        out = capsys.readouterr().out
        # All count lines are present
        for label in (
            "phases:",
            "drains:",
            "artifact contracts:",
            "loop counters:",
            "budget counters:",
        ):
            assert label in out

    def test_missing_directory_returns_one(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        missing = tmp_path / "nonexistent"
        code = check_policy_command(missing)
        err = capsys.readouterr().err
        assert code == 1
        assert "not found" in err

    def test_invalid_pipeline_returns_two(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _copy_defaults(tmp_path)
        # Overwrite pipeline.toml with an invalid transition target
        (tmp_path / "pipeline.toml").write_text(
            "[phases.planning]\n"
            'drain = "planning"\n'
            'prompt_template = "planning.jinja"\n'
            "[phases.planning.transitions]\n"
            'on_success = "no_such_phase"\n'
            "\n"
            "[phases.complete]\n"
            'drain = "complete"\n'
            "[phases.complete.transitions]\n"
            'on_success = "complete"\n'
            'on_loopback = "complete"\n'
        )
        code = check_policy_command(tmp_path)
        err = capsys.readouterr().err
        assert code == _POLICY_VALIDATION_EXIT_CODE
        assert "Policy validation error" in err


def test_agent_session_uses_stored_capability_profile_when_set() -> None:
    identity = MultimodalModelIdentity(provider="claude")
    stored = resolve_capability_profile(identity)
    session = AgentSession(
        session_id="commit-test",
        run_id="run-test",
        drain="commit",
        capabilities=set(),
        model_identity=identity,
        stored_capability_profile=stored,
    )
    assert isinstance(session.capability_profile, ResolvedCapabilityProfile)
    assert session.capability_profile is stored
