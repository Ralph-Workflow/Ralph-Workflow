"""Markdown artifact contracts emitted by pipeline plumbing prompts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.cli.commands import commit as commit_module
from ralph.cli.commands.smoke import build_smoke_prompt
from ralph.config.enums import AgentTransport
from ralph.config.models import UnifiedConfig
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import commit_message, smoke_test_result

if TYPE_CHECKING:
    from pytest import MonkeyPatch


def _fenced_markdown(prompt: str) -> str:
    _before, marker, remainder = prompt.partition("```markdown\n")
    assert marker
    document, closing_marker, _after = remainder.partition("\n```")
    assert closing_marker
    return document


def test_smoke_prompt_submits_a_valid_markdown_artifact() -> None:
    prompt = build_smoke_prompt(
        "tmp/interactive-claude-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_md_artifact",
    )

    document = _fenced_markdown(prompt)
    content, diagnostics = parse_and_validate(
        document,
        get_spec(smoke_test_result.SMOKE_TEST_RESULT_SPEC.artifact_type),
    )

    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert content["output_file"] == "tmp/interactive-claude-smoke/todo-list.js"
    assert 'artifact_type="smoke_test_result"' in prompt
    assert "complete markdown document in the content argument" in prompt.lower()
    assert "smoke_test_result.json" not in prompt
    assert "JSON artifact" not in prompt


def test_agy_smoke_prompt_uses_canonical_markdown_submission() -> None:
    prompt = build_smoke_prompt(
        "tmp/interactive-agy-smoke/todo-list.js",
        submit_artifact_tool_name="ralph_submit_md_artifact",
        transport=AgentTransport.AGY,
    )

    document = _fenced_markdown(prompt)
    _content, diagnostics = parse_and_validate(
        document,
        get_spec(smoke_test_result.SMOKE_TEST_RESULT_SPEC.artifact_type),
    )

    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert "Call `ralph_submit_md_artifact`" in prompt
    assert ".agent/artifacts/" not in prompt
    assert "JSON artifact" not in prompt


def test_commit_retry_uses_valid_markdown_as_submit_content(
    monkeypatch: MonkeyPatch,
    tmp_path: Path,
) -> None:
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
    monkeypatch.setattr(commit_module, "validate_local_model_support", lambda *args, **kwargs: None)

    class FakeBridge:
        @property
        def run_id(self) -> str:
            return "fake-run-id"

        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(commit_module, "start_commit_bridge", lambda _repo_root: FakeBridge())

    prompt_bodies: list[str] = []

    def fake_write_commit_prompt_file(_root: Path, prompt: str) -> str:
        prompt_bodies.append(prompt)
        prompt_file = tmp_path / f"prompt-{len(prompt_bodies)}.md"
        prompt_file.write_text(prompt, encoding="utf-8")
        return str(prompt_file)

    monkeypatch.setattr(commit_module, "write_commit_prompt_file", fake_write_commit_prompt_file)

    invocation_count = 0

    def fake_invoke_agent(
        _agent_config: object,
        _prompt_file: object,
        *_args: object,
        **_kwargs: object,
    ) -> object:
        nonlocal invocation_count
        invocation_count += 1
        if invocation_count <= 2:
            return iter(["agent analyzed the pending diff without submitting"])
        artifact_path = tmp_path / ".agent" / "artifacts" / "commit_message.md"
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_text(
            "---\ntype: commit\nsubject: fix: fallback agent message\n---\n",
            encoding="utf-8",
        )
        return iter([])

    monkeypatch.setattr(commit_module, "invoke_agent", fake_invoke_agent)

    commit_module.commit_plumbing(
        options=commit_module.CommitPlumbingOptions(generate_commit_msg=True)
    )

    retry_prompt = next(
        body for body in prompt_bodies[1:] if "Example MCP arguments: " in body
    )
    example_line = next(
        line
        for line in retry_prompt.splitlines()
        if line.startswith("Example MCP arguments: ")
    )
    arguments = json.loads(example_line.removeprefix("Example MCP arguments: "))
    document = arguments["content"]
    content, diagnostics = parse_and_validate(
        document,
        get_spec(commit_message.COMMIT_MESSAGE_SPEC.artifact_type),
    )

    assert arguments["artifact_type"] == "commit_message"
    assert document.startswith("---\ntype: commit\n")
    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    assert content == {
        "type": "commit",
        "subject": "fix(auth): prevent token expiry race",
    }
