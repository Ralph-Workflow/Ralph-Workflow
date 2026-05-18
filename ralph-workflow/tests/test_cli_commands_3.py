"""Focused CLI command tests for commit, diagnose, init, and option helpers."""

from __future__ import annotations

from io import StringIO
from types import SimpleNamespace
from typing import TYPE_CHECKING

from rich.console import Console

from ralph.cli.commands import commit as commit_module
from ralph.config.models import GeneralConfig
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    ResolvedCapabilityProfile,
)
from ralph.mcp.protocol.session import AgentSession
from ralph.policy.models import AgentChainConfig, AgentDrainConfig

if TYPE_CHECKING:
    import pytest


_SUMMARY_RETRY_FAILURES = 2


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
        def agent_endpoint_uri(self) -> str:
            return "http://127.0.0.1:9999/mcp"

        def shutdown(self) -> None:
            return None

    monkeypatch.setattr(commit_module, "start_commit_bridge", lambda _repo_root: FakeBridge())


def test_agent_session_falls_back_to_resolved_profile_when_not_stored() -> None:
    session = AgentSession(
        session_id="commit-test",
        run_id="run-test",
        drain="commit",
        capabilities=set(),
    )
    profile = session.capability_profile
    assert isinstance(profile, ResolvedCapabilityProfile)
    assert profile.identity == UNKNOWN_IDENTITY
