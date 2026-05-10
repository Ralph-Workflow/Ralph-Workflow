"""Tests: _print_dry_run displays policy entry phase, not the literal 'planning'."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from ralph.cli.commands.run import _print_dry_run
from ralph.config.models import UnifiedConfig
from ralph.display.context import DisplayContext
from ralph.display.theme import RALPH_THEME
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)


def _console() -> Console:
    buf = StringIO()
    return Console(
        file=buf,
        color_system=None,
        force_terminal=False,
        theme=RALPH_THEME,
        width=200,
        highlight=False,
    )


def _display_context(console: Console) -> DisplayContext:
    return DisplayContext(
        console=console,
        theme=RALPH_THEME,
        width=200,
        mode="wide",
        narrow=False,
        color_enabled=False,
        glyphs_enabled=False,
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


def _fake_config() -> UnifiedConfig:
    return UnifiedConfig(
        agent_chains={"design": ["claude"]},
        agent_drains={"design": "design"},
    )


def _custom_policy_bundle(entry_phase: str = "design") -> PolicyBundle:
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={entry_phase: AgentChainConfig(agents=["claude"])},
            agent_drains={entry_phase: AgentDrainConfig(chain=entry_phase)},
        ),
        pipeline=PipelinePolicy(
            entry_phase=entry_phase,
            terminal_phase="done",
            phases={
                entry_phase: PhaseDefinition(
                    drain=entry_phase,
                    role="execution",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "halt": PhaseDefinition(
                    drain="halt",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="halt", on_loopback="halt"),
                ),
                "done": PhaseDefinition(
                    drain="done",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="done", on_loopback="done"),
                ),
            },
            recovery=RecoveryPolicy(failed_route="halt"),
        ),
        artifacts=ArtifactsPolicy(artifacts={}),
    )


def test_dry_run_phase_text_uses_policy_entry_phase() -> None:
    """_print_dry_run shows the policy entry_phase when no initial_state is provided."""
    console = _console()
    ctx = _display_context(console)
    bundle = _custom_policy_bundle(entry_phase="design")
    _print_dry_run(None, _fake_config(), bundle, display_context=ctx)
    output = console.file.getvalue()
    assert "design" in output
    assert "planning" not in output


def test_dry_run_phase_text_falls_back_to_unknown_without_policy() -> None:
    """_print_dry_run shows 'unknown' when policy_bundle is None and no initial_state."""
    console = _console()
    ctx = _display_context(console)
    _print_dry_run(None, _fake_config(), None, display_context=ctx)
    output = console.file.getvalue()
    assert "unknown" in output
    assert "planning" not in output


def test_dry_run_uses_initial_state_phase_when_present() -> None:
    """_print_dry_run shows the initial_state.phase even when policy_bundle is provided."""
    console = _console()
    ctx = _display_context(console)
    bundle = _custom_policy_bundle(entry_phase="design")
    # Build a minimal PipelineState with phase="build"
    state = PipelineState(phase="build")
    _print_dry_run(state, _fake_config(), bundle, display_context=ctx)
    output = console.file.getvalue()
    assert "build" in output
    assert "design" not in output
