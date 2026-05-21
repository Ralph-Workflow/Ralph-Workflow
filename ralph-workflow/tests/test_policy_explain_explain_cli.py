"""Tests for the policy explanation feature (ralph/policy/explain.py and render.py)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from ralph.cli.commands.check_policy import check_policy_command
from ralph.cli.commands.explain import explain_command
from ralph.cli.main import app
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)

_DEFAULT_POLICY_DIR = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"

_CHECK_LOOP_MAX = 3


def _minimal_bundle(phases: dict[str, PhaseDefinition] | None = None) -> PolicyBundle:
    """Build a minimal PolicyBundle for testing."""
    if phases is None:
        phases = {
            "work": PhaseDefinition(
                drain="planning",
                role="execution",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done"),
            ),
        }
    return PolicyBundle(
        agents=AgentsPolicy(
            agent_chains={"main_chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "planning": AgentDrainConfig(chain="main_chain"),
                "complete": AgentDrainConfig(chain="main_chain"),
            },
        ),
        pipeline=PipelinePolicy(
            phases=phases,
            entry_phase="work",
            terminal_phase="done",
        ),
        artifacts=ArtifactsPolicy(),
    )


class TestExplainCLI:
    def test_explain_command_exits_0(self) -> None:

        rc = explain_command(_DEFAULT_POLICY_DIR)
        assert rc == 0

    def test_explain_command_default_policy_dir(self) -> None:

        rc = explain_command(None)
        assert rc == 0

    def test_explain_command_invalid_dir_returns_1(self) -> None:

        rc = explain_command(Path("/nonexistent/policy/dir"))
        assert rc == 1

    def test_cli_explain_policy_flag(self) -> None:

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["--explain-policy", "--explain-policy-dir", str(_DEFAULT_POLICY_DIR)],
        )
        assert result.exit_code == 0
        assert "planning" in result.output.lower() or "PHASES" in result.output

    def test_cli_explain_policy_shows_block_lifecycle_metadata(self) -> None:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["--explain-policy", "--explain-policy-dir", str(_DEFAULT_POLICY_DIR)],
        )

        assert result.exit_code == 0
        assert "Entry block" in result.output
        assert "developer_iteration" in result.output
        assert "development_final_commit" in result.output

    def test_check_policy_command_reports_block_summary(self, capsys: object) -> None:
        rc = check_policy_command(_DEFAULT_POLICY_DIR)

        captured = capsys.readouterr()
        assert rc == 0
        assert "blocks:" in captured.out
        assert "lifecycle completion phases:" in captured.out
