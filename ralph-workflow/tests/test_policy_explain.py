"""Tests for the policy explanation feature (ralph/policy/explain.py and render.py)."""

from __future__ import annotations

from pathlib import Path

from ralph.policy.explain import (
    PolicyExplanation,
    explain_policy,
)
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactsPolicy,
    BudgetCounterConfig,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
)
from ralph.policy.render import render_explanation_text

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


class TestExplainPolicy:
    def test_returns_policy_explanation(self) -> None:
        bundle = _minimal_bundle()
        result = explain_policy(bundle)
        assert isinstance(result, PolicyExplanation)

    def test_entry_and_terminal_phase(self) -> None:
        bundle = _minimal_bundle()
        result = explain_policy(bundle)
        assert result.entry_phase == "work"
        assert result.terminal_phase == "done"

    def test_phases_list_contains_all_phases(self) -> None:
        bundle = _minimal_bundle()
        result = explain_policy(bundle)
        names = [p.name for p in result.phases]
        assert "work" in names
        assert "done" in names

    def test_entry_phase_flag_set(self) -> None:
        bundle = _minimal_bundle()
        result = explain_policy(bundle)
        entry = next(p for p in result.phases if p.name == "work")
        terminal = next(p for p in result.phases if p.name == "done")
        assert entry.is_entry is True
        assert terminal.is_entry is False

    def test_terminal_phase_flag_set(self) -> None:
        bundle = _minimal_bundle()
        result = explain_policy(bundle)
        terminal = next(p for p in result.phases if p.name == "done")
        assert terminal.is_terminal is True

    def test_phase_agents_populated(self) -> None:
        bundle = _minimal_bundle()
        result = explain_policy(bundle)
        work = next(p for p in result.phases if p.name == "work")
        assert work.agents == ["claude"]
        assert work.chain == "main_chain"

    def test_phase_transitions_populated(self) -> None:
        bundle = _minimal_bundle()
        result = explain_policy(bundle)
        work = next(p for p in result.phases if p.name == "work")
        assert work.on_success == "done"
        assert work.on_failure is None

    def test_loop_policy_explanation(self) -> None:
        pipeline = PipelinePolicy(
            phases={
                "start": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="check"),
                ),
                "check": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(on_success="done", on_loopback="start"),
                    loop_policy=PhaseLoopPolicy(
                        max_iterations=_CHECK_LOOP_MAX,
                        iteration_state_field="check_loop",
                    ),
                    decisions={
                        "approve": PhaseDecisionRoute(target="done"),
                    },
                ),
                "done": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="done"),
                ),
            },
            entry_phase="start",
            terminal_phase="done",
            loop_counters={
                "check_loop": LoopCounterConfig(
                    default_max=_CHECK_LOOP_MAX,
                    description="check loop",
                )
            },
        )
        bundle = PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"c": AgentChainConfig(agents=["claude"])},
                agent_drains={
                    "planning": AgentDrainConfig(chain="c"),
                    "development_analysis": AgentDrainConfig(chain="c"),
                    "complete": AgentDrainConfig(chain="c"),
                },
            ),
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(
                artifacts={
                    "check_review": ArtifactContract(
                        drain="development_analysis",
                        artifact_type="check_review",
                        decision_vocabulary=["approve"],
                    ),
                }
            ),
        )
        result = explain_policy(bundle)
        check = next(p for p in result.phases if p.name == "check")
        assert check.loop_policy is not None
        assert check.loop_policy.max_iterations == _CHECK_LOOP_MAX
        assert check.loop_policy.iteration_state_field == "check_loop"

        assert len(result.loop_counters) == 1
        assert result.loop_counters[0].name == "check_loop"
        assert result.loop_counters[0].default_max == _CHECK_LOOP_MAX

    def test_budget_counter_explanation(self) -> None:
        pipeline = PipelinePolicy(
            phases={
                "do": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(on_success="done"),
                    commit_policy=PhaseCommitPolicy(increments_counter="build_pass"),
                ),
                "done": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="done"),
                ),
            },
            entry_phase="do",
            terminal_phase="done",
            budget_counters={
                "build_pass": BudgetCounterConfig(description="build passes")
            },
        )
        bundle = PolicyBundle(
            agents=AgentsPolicy(
                agent_chains={"c": AgentChainConfig(agents=["claude"])},
                agent_drains={
                    "planning": AgentDrainConfig(chain="c"),
                    "development_commit": AgentDrainConfig(chain="c"),
                    "complete": AgentDrainConfig(chain="c"),
                },
            ),
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(),
        )
        result = explain_policy(bundle)
        assert len(result.budget_counters) == 1
        assert result.budget_counters[0].name == "build_pass"

        commit = next(p for p in result.phases if p.name == "commit")
        assert commit.commit_policy is not None
        assert commit.commit_policy.increments_counter == "build_pass"

    def test_default_policy_explains_all_phases(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        result = explain_policy(bundle)
        phase_names = {p.name for p in result.phases}
        assert "planning" in phase_names
        assert "development" in phase_names
        assert "review" in phase_names
        assert "fix" in phase_names


class TestRenderExplanationText:
    def test_output_is_string(self) -> None:
        bundle = _minimal_bundle()
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_entry_phase_appears(self) -> None:
        bundle = _minimal_bundle()
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "work" in text
        assert "Entry phase" in text

    def test_terminal_phase_appears(self) -> None:
        bundle = _minimal_bundle()
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "done" in text
        assert "Terminal phase" in text or "TERMINAL" in text

    def test_default_policy_renders_phase_names(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        for name in ["planning", "development", "review", "fix"]:
            assert name in text, f"Phase '{name}' not found in rendered output"

    def test_answers_what_happens_after_success(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "On success" in text

    def test_answers_when_retry_vs_fallback(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "retry" in text.lower() or "retries" in text.lower()

    def test_answers_when_commit_required(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "commit" in text.lower()

    def test_answers_when_parallel_execution_allowed(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "parallel" in text.lower()

    def test_recovery_policy_appears(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        assert "RECOVERY" in text or "recovery" in text.lower()

    def test_loop_counters_section(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        if exp.loop_counters:
            assert "LOOP COUNTERS" in text

    def test_budget_counters_section(self) -> None:
        bundle = load_policy(_DEFAULT_POLICY_DIR)
        exp = explain_policy(bundle)
        text = render_explanation_text(exp)
        if exp.budget_counters:
            assert "BUDGET COUNTERS" in text


class TestExplainCLI:
    def test_explain_command_exits_0(self) -> None:
        from ralph.cli.commands.explain import explain_command  # noqa: PLC0415

        rc = explain_command(_DEFAULT_POLICY_DIR)
        assert rc == 0

    def test_explain_command_default_policy_dir(self) -> None:
        from ralph.cli.commands.explain import explain_command  # noqa: PLC0415

        rc = explain_command(None)
        assert rc == 0

    def test_explain_command_invalid_dir_returns_1(self) -> None:
        from ralph.cli.commands.explain import explain_command  # noqa: PLC0415

        rc = explain_command(Path("/nonexistent/policy/dir"))
        assert rc == 1

    def test_cli_explain_policy_flag(self) -> None:
        from typer.testing import CliRunner  # noqa: PLC0415

        from ralph.cli.main import app  # noqa: PLC0415

        runner = CliRunner()
        result = runner.invoke(
            app,
            ["--explain-policy", "--explain-policy-dir", str(_DEFAULT_POLICY_DIR)],
        )
        assert result.exit_code == 0
        assert "planning" in result.output.lower() or "PHASES" in result.output
