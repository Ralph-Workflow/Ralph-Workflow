"""P2 (wt-028-display S-20 / AC-13) commit plumbing fold tests.

The private ``_render_commit_agent_activity_line`` and its
helpers (``_styled_commit_prefix``, ``_tool_input_summary``,
``_format_agent_invocation_failure``, ``_format_commit_agent_failure``)
must route through the shared ``render_event`` registry. No
command reaches the display through a private path.

These tests pin:

* the legacy ``_styled_commit_prefix`` and ``_tool_input_summary``
  helpers are GONE (DA-005, AC-13, S-32),
* the public ``_render_commit_agent_activity_line`` calls the
  shared registry (no private formatting helpers),
* the rendered text comes out of ``render_event`` (same path as
  every other code path that emits agent activity),
* the parser-line types ``text`` / ``tool_use`` / ``tool_result`` /
  ``error`` / unknown all funnel through the registry.
"""

from __future__ import annotations

import pytest
from rich.text import Text

from ralph.agents.parsers.agent_output_line import AgentOutputLine
from ralph.pipeline.plumbing import commit_plumbing


def test_commit_plumbing_drops_styled_commit_prefix() -> None:
    """The private ``_styled_commit_prefix`` helper is retired (DA-005)."""
    assert not hasattr(commit_plumbing, "_styled_commit_prefix"), (
        "_styled_commit_prefix must be removed: the commit path is "
        "folded into the shared render_event registry"
    )


def test_commit_plumbing_drops_tool_input_summary() -> None:
    """The private ``_tool_input_summary`` helper is retired (DA-005)."""
    assert not hasattr(commit_plumbing, "_tool_input_summary"), (
        "_tool_input_summary must be removed: the shared registry "
        "produces tool-input summaries via format_tool_input"
    )


def test_commit_renders_text_via_shared_registry() -> None:
    """A ``text`` line flows through ``render_event`` (not a private path)."""
    line = AgentOutputLine(type="text", content="hello world", metadata={})
    rendered = commit_plumbing._render_commit_agent_activity_line(
        line, agent_name="claude"
    )
    assert isinstance(rendered, Text)
    plain = rendered.plain
    assert "hello world" in plain


def test_commit_renders_tool_use_via_shared_registry() -> None:
    """A ``tool_use`` line is rendered by the shared registry."""
    line = AgentOutputLine(
        type="tool_use",
        content="read",
        metadata={"input": {"path": "/tmp/foo.py"}},
    )
    rendered = commit_plumbing._render_commit_agent_activity_line(
        line, agent_name="claude"
    )
    assert isinstance(rendered, Text)
    plain = rendered.plain
    # The shared registry produces a tool name + an input summary;
    # the exact format is the registry's contract, not a private one.
    assert "read" in plain


def test_commit_renders_tool_result_via_shared_registry() -> None:
    """A ``tool_result`` line is rendered by the shared registry."""
    line = AgentOutputLine(
        type="tool_result",
        content="ok",
        metadata={},
    )
    rendered = commit_plumbing._render_commit_agent_activity_line(
        line, agent_name="claude"
    )
    assert isinstance(rendered, Text)
    plain = rendered.plain
    assert "ok" in plain


def test_commit_renders_error_via_shared_registry() -> None:
    """An ``error`` line is rendered by the shared registry."""
    line = AgentOutputLine(type="error", content="boom", metadata={})
    rendered = commit_plumbing._render_commit_agent_activity_line(
        line, agent_name="claude"
    )
    assert isinstance(rendered, Text)
    plain = rendered.plain
    assert "boom" in plain


def test_commit_renders_unknown_via_shared_registry() -> None:
    """An unrecognized parser type falls back to the shared UNKNOWN renderer."""
    line = AgentOutputLine(
        type="future_kind_we_dont_know_about",
        content="payload",
        metadata={},
    )
    rendered = commit_plumbing._render_commit_agent_activity_line(
        line, agent_name="claude"
    )
    assert isinstance(rendered, Text)
    # The shared registry's UNKNOWN handler is the design path for
    # unrecognized events: it still produces a Text so the operator
    # gets something rather than a raw dump.
    assert rendered.plain != ""


def test_commit_renderer_returns_none_for_empty_text() -> None:
    """An empty text line is dropped (legacy contract)."""
    line = AgentOutputLine(type="text", content="   ", metadata={})
    rendered = commit_plumbing._render_commit_agent_activity_line(
        line, agent_name="claude"
    )
    assert rendered is None


@pytest.mark.parametrize("agent_name", ["claude", "codex", "opencode", "pi", "agy"])
def test_commit_renderer_does_not_crash_for_known_agents(agent_name: str) -> None:
    """The shared registry handles every documented agent identity."""
    line = AgentOutputLine(type="text", content="ping", metadata={})
    rendered = commit_plumbing._render_commit_agent_activity_line(
        line, agent_name=agent_name
    )
    assert isinstance(rendered, Text)
    assert "ping" in rendered.plain
