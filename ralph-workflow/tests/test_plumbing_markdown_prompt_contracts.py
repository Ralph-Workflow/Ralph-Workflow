"""Markdown artifact contracts emitted by pipeline plumbing prompts."""

from __future__ import annotations

import json

from ralph.cli.commands import commit as _commit_command
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
from ralph.mcp.artifacts.markdown.specs import commit_message, smoke_test_result
from ralph.pipeline.plumbing.commit_plumbing import _summarized_retry_prompt
from ralph.pipeline.plumbing.smoke_plumbing import _build_smoke_prompt

_COMMIT_IMPORT_GUARD = _commit_command.CommitAgentResult


def _fenced_markdown(prompt: str) -> str:
    _before, marker, remainder = prompt.partition("```markdown\n")
    assert marker
    document, closing_marker, _after = remainder.partition("\n```")
    assert closing_marker
    return document


def test_smoke_prompt_submits_a_valid_markdown_artifact() -> None:
    prompt = _build_smoke_prompt(
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
    prompt = _build_smoke_prompt(
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


def test_commit_retry_uses_valid_markdown_as_submit_content() -> None:
    prompt = _summarized_retry_prompt(
        "Original prompt",
        ["Analyzed the pending diff."],
        AgentConfig(cmd="opencode", transport=AgentTransport.OPENCODE),
    )

    example_line = next(
        line for line in prompt.splitlines() if line.startswith("Example MCP arguments: ")
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
