from __future__ import annotations

import functools
import importlib
import shutil
from dataclasses import fields
from pathlib import Path
from textwrap import dedent
from unittest.mock import MagicMock

import pytest

from ralph.cli.commands.init import STARTER_PROMPT_SENTINEL
from ralph.git.diff_entry import DiffEntry
from ralph.mcp.protocol.capability_mapping import drain_class_for_session
from ralph.phases.commit_review import TripwireThresholds, evaluate_tripwires
from ralph.phases.required_artifacts import (
    RequiredArtifact,
    resolve_phase_required_artifact,
)
from ralph.pipeline.progress import (
    advance_phase,
    apply_commit_outcome,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import (
    PolicyValidationError as LoaderPolicyValidationError,
)
from ralph.policy.loader import (
    load_policy,
)
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactContract,
    ArtifactHistoryPolicy,
    ArtifactsPolicy,
    BudgetCounterConfig,
    CommitReviewPolicy,
    DrainName,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDecisionRoute,
    PhaseDefinition,
    PhaseInvocationGate,
    PhaseLoopPolicy,
    PhaseParallelization,
    PhaseTransition,
    PhaseVerificationPolicy,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)
from ralph.policy.validation import (
    CheckpointPolicyMismatchError,
    PolicyValidationError,
    get_drain_resolution_matrix,
    validate_chain_exists,
    validate_checkpoint_compatible,
    validate_drain_bound,
    validate_drain_contracts,
    validate_phase_exists_in_policy,
    validate_policy_completeness,
    validate_required_inputs,
)

DEFAULT_MAX_WORK_UNITS = 50

ValidationError = importlib.import_module("pydantic").ValidationError


@functools.lru_cache(maxsize=1)
def _cached_default_bundle() -> PolicyBundle:
    default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
    return load_policy(default_dir)


def _strict_cli_counter_overrides_minimal_agents(drains: list[str]) -> AgentsPolicy:
    chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
    agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
    return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)


def _strict_cli_counter_overrides_terminal_phase(
    drain: str = "complete", outcome: str = "success"
) -> PhaseDefinition:
    return PhaseDefinition(
        drain=drain,
        role="terminal",
        terminal_outcome=outcome,
        transitions=PhaseTransition(on_success=drain, on_loopback=drain),
    )


def _strict_cli_counter_overrides_minimal_bundle_with_phases(
    phases: dict[str, PhaseDefinition],
) -> PolicyBundle:
    drains = list(phases.keys())
    agents = _strict_cli_counter_overrides_minimal_agents(drains)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase=drains[0],
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


def _strict_legacy_fields_rejected_minimal_agents(drains: list[str]) -> AgentsPolicy:
    chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
    agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
    return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)


def _strict_legacy_fields_rejected_terminal_phase(
    drain: str = "complete", outcome: str = "success"
) -> PhaseDefinition:
    return PhaseDefinition(
        drain=drain,
        role="terminal",
        terminal_outcome=outcome,
        transitions=PhaseTransition(on_success=drain, on_loopback=drain),
    )


def _strict_legacy_fields_rejected_minimal_bundle_with_phases(
    phases: dict[str, PhaseDefinition],
) -> PolicyBundle:
    drains = list(phases.keys())
    agents = _strict_legacy_fields_rejected_minimal_agents(drains)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase=drains[0],
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


def _strict_skip_invocation_has_on__minimal_agents(drains: list[str]) -> AgentsPolicy:
    chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
    agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
    return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)


def _strict_skip_invocation_has_on__terminal_phase(
    drain: str = "complete", outcome: str = "success"
) -> PhaseDefinition:
    return PhaseDefinition(
        drain=drain,
        role="terminal",
        terminal_outcome=outcome,
        transitions=PhaseTransition(on_success=drain, on_loopback=drain),
    )


def _strict_skip_invocation_has_on__minimal_bundle_with_phases(
    phases: dict[str, PhaseDefinition],
) -> PolicyBundle:
    drains = list(phases.keys())
    agents = _strict_skip_invocation_has_on__minimal_agents(drains)
    pipeline = PipelinePolicy(
        phases=phases,
        entry_phase=drains[0],
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="complete"),
    )
    return PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={}))


class TestAdvancePhaseRequiresPolicyForCommitTargets:
    def test_raises_value_error_when_policy_is_none(self) -> None:

        state = PipelineState(phase="development_commit")
        with pytest.raises(ValueError, match="requires PipelinePolicy"):
            advance_phase(state, "development", policy=None)


class TestAgentsPolicyValidation:
    def test_valid_agents_policy(self) -> None:
        policy = AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"], max_retries=2),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )
        assert policy.agent_chains["planning"].agents == ["claude"]
        assert policy.agent_drains["planning"].chain == "planning"

    def test_drain_references_unknown_chain_raises(self) -> None:
        with pytest.raises(ValueError, match="references unknown chain"):
            AgentsPolicy(
                agent_chains={},
                agent_drains={
                    "planning": AgentDrainConfig(chain="nonexistent"),
                },
            )

    def test_empty_chain_list_raises(self) -> None:
        with pytest.raises(ValidationError, match="too_short"):
            AgentChainConfig(agents=[])

    def test_chain_referencing_unknown_agent_raises(self) -> None:
        chain = AgentChainConfig(agents=["nonexistent_agent"], max_retries=2)
        assert chain.agents == ["nonexistent_agent"]


class TestApplyCommitOutcomeRequiresPolicy:
    def test_raises_value_error_when_policy_is_none(self) -> None:
        state = PipelineState(phase="development_commit")
        advanced = PipelineState(phase="development")
        with pytest.raises(ValueError, match="requires PipelinePolicy"):
            apply_commit_outcome(state, advanced, skipped=False, policy=None)


class TestCheckpointPolicyMismatchError:
    def test_error_message_contains_phase(self) -> None:
        error = CheckpointPolicyMismatchError(
            checkpoint_phase="test_phase",
            valid_phases={"phase_a", "phase_b"},
        )
        assert "test_phase" in str(error)
        assert "phase_a" in str(error)
        assert "phase_b" in str(error)
        assert "--no-resume" in str(error)


class TestDefaultPolicyLoading:
    def test_load_default_policy_succeeds(self) -> None:
        bundle = _cached_default_bundle()

        assert bundle.agents is not None
        assert bundle.pipeline is not None
        assert bundle.artifacts is not None

        expected_drains = {
            "planning",
            "development",
            "development_analysis",
            "development_commit",
        }
        actual_drains = set(bundle.agents.agent_drains.keys())
        assert expected_drains.issubset(actual_drains), (
            f"Missing drains: {expected_drains - actual_drains}"
        )
        assert bundle.pipeline.entry_block == "developer_iteration"
        assert bundle.pipeline.entry_phase == "planning"
        assert bundle.pipeline.terminal_phase == "complete"

    def test_parallel_policy_roundtrip_retains_legacy_settings_without_capping_workers(
        self,
    ) -> None:
        bundle = _cached_default_bundle()

        assert bundle.pipeline.phases["development"].parallelization is not None
        dev_para = bundle.pipeline.phases["development"].parallelization
        assert dev_para.max_work_units == DEFAULT_MAX_WORK_UNITS

        phases = dict(bundle.pipeline.phases)
        phases["development"] = phases["development"].model_copy(
            update={
                "parallelization": PhaseParallelization(
                    max_parallel_workers=8,
                    max_work_units=1,
                    require_allowed_directories=True,
                )
            }
        )
        configured = bundle.model_copy(
            update={"pipeline": bundle.pipeline.model_copy(update={"phases": phases})}
        )
        restored = PolicyBundle.model_validate_json(configured.model_dump_json())
        validate_policy_completeness(restored)
        parallelization = restored.pipeline.phases["development"].parallelization
        assert parallelization is not None
        assert parallelization.max_parallel_workers == 8
        assert parallelization.max_work_units == 1

    def test_all_pipeline_drains_are_bound(self) -> None:
        bundle = _cached_default_bundle()

        for phase_name, phase_def in bundle.pipeline.phases.items():
            if phase_def.role == "terminal":
                continue
            assert phase_def.drain in bundle.agents.agent_drains, (
                f"Phase '{phase_name}' uses unbound drain '{phase_def.drain}'"
            )

    def test_development_commit_review_phase_in_default_policy(self) -> None:
        bundle = _cached_default_bundle()

        pre_analysis_cleanup = bundle.pipeline.phases["development_commit_review"]
        final_cleanup = bundle.pipeline.phases["development_final_commit_review"]
        development = bundle.pipeline.phases["development"]
        dev_analysis = bundle.pipeline.phases["development_analysis"]

        assert pre_analysis_cleanup.role == "commit_review"
        assert pre_analysis_cleanup.drain == "commit"
        assert final_cleanup.role == "commit_review"
        assert final_cleanup.drain == "commit"

        assert development.transitions.on_success == "development_commit_review"
        assert dev_analysis.transitions.on_success == "development_final_commit_review"
        assert dev_analysis.decisions["completed"].target == "development_final_commit_review"

        for phase in (pre_analysis_cleanup, final_cleanup):
            assert phase.loop_policy is not None, "commit review phases must declare a loop_policy"
            assert phase.loop_policy.iteration_state_field == "commit_review_iteration", (
                f"loop_policy.iteration_state_field must be 'commit_review_iteration', "
                f"got: {phase.loop_policy.iteration_state_field}"
            )

    def test_default_policy_uses_pre_analysis_and_final_commit_paths(self) -> None:
        bundle = _cached_default_bundle()

        pre_analysis_cleanup = bundle.pipeline.phases["development_commit_review"]
        final_cleanup = bundle.pipeline.phases["development_final_commit_review"]
        final_commit = bundle.pipeline.phases["development_final_commit"]
        pre_analysis_commit = bundle.pipeline.phases["development_commit"]

        assert pre_analysis_cleanup.transitions.on_success == "development_commit"
        assert final_cleanup.transitions.on_success == "development_final_commit"
        assert pre_analysis_commit.commit_policy is not None
        assert pre_analysis_commit.commit_policy.increments_counter is None
        assert pre_analysis_commit.commit_policy.skipped_advances_progress is False
        assert final_commit.role == "commit"
        assert final_commit.drain == "development_commit"
        assert final_commit.commit_policy is not None
        assert final_commit.commit_policy.skipped_advances_progress is True
        assert final_commit.commit_policy.increments_counter == "iteration"

    def test_commit_loop_resets_follow_compiled_hook_semantics(self) -> None:
        bundle = _cached_default_bundle()

        pre_analysis_commit = bundle.pipeline.phases["development_commit"]
        assert pre_analysis_commit.commit_policy is not None
        assert pre_analysis_commit.commit_policy.loop_resets == ["commit_review_iteration"]

        final_commit = bundle.pipeline.phases["development_final_commit"]
        assert final_commit.commit_policy is not None
        loop_resets = final_commit.commit_policy.loop_resets
        assert "commit_review_iteration" in loop_resets
        assert "development_analysis_iteration" in loop_resets


class TestForbidSiblingDrainInference:
    def test_validate_drain_contracts_all_bound_passes(self) -> None:
        policy = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
                "development": AgentChainConfig(agents=["claude"]),
                "development_analysis": AgentChainConfig(agents=["claude"]),
                "development_commit": AgentChainConfig(agents=["claude"]),
                "review": AgentChainConfig(agents=["claude"]),
                "review_analysis": AgentChainConfig(agents=["claude"]),
                "fix": AgentChainConfig(agents=["claude"]),
                "review_commit": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning", drain_class="planning"),
                "development": AgentDrainConfig(chain="development", drain_class="development"),
                "development_analysis": AgentDrainConfig(
                    chain="development_analysis", drain_class="analysis"
                ),
                "development_commit": AgentDrainConfig(
                    chain="development_commit", drain_class="commit"
                ),
                "review": AgentDrainConfig(chain="review", drain_class="review"),
                "review_analysis": AgentDrainConfig(
                    chain="review_analysis", drain_class="analysis"
                ),
                "fix": AgentDrainConfig(chain="fix", drain_class="fix"),
                "review_commit": AgentDrainConfig(chain="review_commit", drain_class="commit"),
            },
        )

        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="development_analysis"),
                ),
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    transitions=PhaseTransition(on_success="development_commit"),
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    transitions=PhaseTransition(on_success="review"),
                ),
                "review": PhaseDefinition(
                    drain="review",
                    transitions=PhaseTransition(on_success="review_analysis"),
                ),
                "review_analysis": PhaseDefinition(
                    drain="review_analysis",
                    transitions=PhaseTransition(on_success="review_commit"),
                ),
                "fix": PhaseDefinition(
                    drain="fix",
                    transitions=PhaseTransition(on_success="review"),
                ),
                "review_commit": PhaseDefinition(
                    drain="review_commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(
            agents=policy,
            pipeline=pipeline,
            artifacts=artifacts,
        )

        validate_drain_contracts(bundle)

    def test_validate_drain_contracts_ignores_unused_canonical_drains(self) -> None:
        policy = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
                "development": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning", drain_class="planning"),
                "development": AgentDrainConfig(chain="development", drain_class="development"),
            },
        )

        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(
            agents=policy,
            pipeline=pipeline,
            artifacts=artifacts,
        )

        validate_drain_contracts(bundle)

    def test_validate_drain_contracts_pipeline_drain_unbound_raises(self) -> None:
        policy = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )

        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development_analysis"),
                ),
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle.model_construct(
            agents=policy,
            pipeline=pipeline,
            artifacts=artifacts,
        )

        with pytest.raises(
            PolicyValidationError, match="pipeline drains lack explicit chain bindings"
        ):
            validate_drain_contracts(bundle)

    def test_validate_drain_contracts_flag_false_skips_validation(self) -> None:
        policy = AgentsPolicy(
            forbid_sibling_drain_inference=False,
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )

        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(
            agents=policy,
            pipeline=pipeline,
            artifacts=artifacts,
        )

        validate_drain_contracts(bundle)

    def test_forbid_sibling_drain_inference_default_false(self) -> None:
        policy = AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )
        assert policy.forbid_sibling_drain_inference is False


class TestGetDrainResolutionMatrix:
    def test_empty_matrix(self) -> None:
        bundle = _cached_default_bundle()
        matrix = get_drain_resolution_matrix(bundle)
        assert isinstance(matrix, dict)
        assert len(matrix) > 0

    def test_matrix_contains_drain_info(self) -> None:
        bundle = _cached_default_bundle()
        matrix = get_drain_resolution_matrix(bundle)

        if "planning" in matrix:
            assert "chain" in matrix["planning"]
            assert "agents" in matrix["planning"]
            assert "max_retries" in matrix["planning"]


class TestLoadPolicyForbidSiblingInference:
    def test_load_policy_rejects_missing_drains(self, tmp_path: Path) -> None:
        config_dir = tmp_path / ".agent"
        config_dir.mkdir(parents=True)

        agents_toml = dedent(
            """
            forbid_sibling_drain_inference = true

            [agent_chains.planning]
            agents = ["claude"]

            [agent_drains.planning]
            chain = "planning"
            drain_class = "planning"
            # custom_analysis drain intentionally absent everywhere
            """
        )
        (config_dir / "agents.toml").write_text(agents_toml)

        pipeline_toml = dedent(
            """
            [phases.planning]
            drain = "planning"
            role = "execution"
            [phases.planning.transitions]
            on_success = "custom_analysis"

            [phases.custom_analysis]
            drain = "custom_analysis"
            role = "execution"
            [phases.custom_analysis.transitions]
            on_success = "complete"

            [phases.complete]
            drain = "planning"
            role = "terminal"
            terminal_outcome = "success"
            [phases.complete.transitions]
            on_success = "complete"
            on_loopback = "complete"

            entry_phase = "planning"
            terminal_phase = "complete"
            """
        )
        (config_dir / "pipeline.toml").write_text(pipeline_toml)

        with pytest.raises(
            LoaderPolicyValidationError,
            match="unbound drains",
        ):
            load_policy(config_dir)


class TestPipelineOwnedArtifactRequiredPolicy:
    def test_default_policy_loads_with_development_phase_required(self) -> None:
        bundle = _cached_default_bundle()
        assert bundle.pipeline.phases["development"].artifact_required is True, (
            "phases.development.artifact_required must be True in default policy"
        )

    def test_artifact_contract_rejects_phase_owned_artifact_required(self) -> None:
        with pytest.raises(ValueError, match=r"pipeline\.toml"):
            ArtifactContract.model_validate(
                {
                    "drain": "development",
                    "artifact_type": "development_result",
                    "artifact_required": False,
                }
            )

    def test_artifact_contract_does_not_publish_retired_json_path_override(self) -> None:
        assert "artifact_json_path" not in ArtifactContract.model_json_schema()["properties"]

    def test_required_artifact_regression_uses_format_neutral_artifact_path(self) -> None:
        field_names = {field.name for field in fields(RequiredArtifact)}

        assert "artifact_path" in field_names
        assert "json_path" not in field_names

    def test_phase_required_artifact_uses_pipeline_owned_required_flag(self) -> None:
        bundle = _cached_default_bundle()
        dev_ra = resolve_phase_required_artifact(
            bundle.pipeline,
            bundle.artifacts,
            phase="development",
            drain="development",
        )
        assert dev_ra is not None, "development phase must have a RequiredArtifact entry"
        assert dev_ra.artifact_required is True, (
            "RequiredArtifact for development must have artifact_required=True from pipeline"
        )


class TestPolicyBundleValidation:
    def test_pipeline_drain_not_bound_raises(self) -> None:

        agents = AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )

        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        artifacts = ArtifactsPolicy(artifacts={})
        with pytest.raises(ValueError, match="unbound drains"):
            PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)

    def test_analysis_phase_without_vocabulary_raises(self) -> None:
        agents = AgentsPolicy(
            agent_chains={
                "development": AgentChainConfig(agents=["claude"]),
                "development_analysis": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "development": AgentDrainConfig(chain="development"),
                "development_analysis": AgentDrainConfig(chain="development_analysis"),
            },
        )

        pipeline = PipelinePolicy(
            phases={
                "development": PhaseDefinition(
                    drain="development",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development",
            terminal_phase="complete",
        )

        artifacts = ArtifactsPolicy(artifacts={})
        with pytest.raises(ValueError, match="decision_vocabulary"):
            PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)


class TestSharedDrainHistoryConsistency:
    def _minimal_bundle(
        self,
        *,
        history_a: ArtifactHistoryPolicy | None,
        history_b: ArtifactHistoryPolicy | None,
    ) -> PolicyBundle:
        agents = AgentsPolicy(
            agent_chains={"mychain": AgentChainConfig(agents=["claude"])},
            agent_drains={"shared_drain": AgentDrainConfig(chain="mychain")},
        )
        pipeline = PipelinePolicy(
            entry_phase="phase_a",
            terminal_phase="complete",
            phases={
                "phase_a": PhaseDefinition(
                    drain="shared_drain",
                    role="execution",
                    transitions=PhaseTransition(on_success="phase_b", on_failure="failed_terminal"),
                    artifact_history=history_a,
                ),
                "phase_b": PhaseDefinition(
                    drain="shared_drain",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete", on_failure="failed_terminal"
                    ),
                    artifact_history=history_b,
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="failed_terminal",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
            },
            recovery=RecoveryPolicy(failed_route="failed_terminal"),
        )
        return PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )

    def test_conflicting_history_enabled_on_same_drain_raises(self) -> None:

        bundle = self._minimal_bundle(
            history_a=ArtifactHistoryPolicy(enabled=True),
            history_b=ArtifactHistoryPolicy(enabled=False),
        )

        with pytest.raises(PolicyValidationError, match=r"artifact_history\.enabled"):
            validate_policy_completeness(bundle)

    def test_consistent_history_enabled_on_same_drain_passes(self) -> None:

        bundle = self._minimal_bundle(
            history_a=ArtifactHistoryPolicy(enabled=True),
            history_b=ArtifactHistoryPolicy(enabled=True),
        )
        validate_policy_completeness(bundle)

    def test_no_artifact_history_on_phase_is_excluded_from_check(self) -> None:

        bundle = self._minimal_bundle(
            history_a=ArtifactHistoryPolicy(enabled=True),
            history_b=None,
        )
        validate_policy_completeness(bundle)


class TestCliCounterOverrides:
    def _bundle_with_budget_counter(self, counter_name: str) -> PolicyBundle:
        agents = _strict_cli_counter_overrides_minimal_agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": _strict_cli_counter_overrides_terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={counter_name: BudgetCounterConfig(tracks_budget=False, default_max=0)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        return PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )

    def test_unknown_counter_override_raises(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        with pytest.raises(PolicyValidationError, match="unknown_counter"):
            validate_policy_completeness(bundle, cli_counter_overrides={"unknown_counter": 3})

    def test_declared_counter_override_passes(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(bundle, cli_counter_overrides={"my_counter": 3})

    def test_no_overrides_kwarg_does_not_validate_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(bundle)

    def test_none_overrides_does_not_validate_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("my_counter")
        validate_policy_completeness(bundle, cli_counter_overrides=None)

    def test_error_message_lists_declared_counters(self) -> None:
        bundle = self._bundle_with_budget_counter("declared_counter")
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle, cli_counter_overrides={"bad_counter": 1})
        error_msg = str(exc_info.value)
        assert "bad_counter" in error_msg
        assert "declared_counter" in error_msg


class TestLegacyFieldsRejected:
    def test_failed_route_failed_alias_rejected(self) -> None:
        with pytest.raises(ValueError, match="'failed' is no longer accepted"):
            RecoveryPolicy(failed_route="failed")

    def test_drain_class_substring_inference_rejected(self) -> None:
        agents = AgentsPolicy(
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={"custom_fixer_drain": AgentDrainConfig(chain="chain")},
        )
        with pytest.raises(PolicyValidationError):
            drain_class_for_session("custom_fixer_drain", agents)

    def test_drain_class_missing_rejected_under_strict_validation(self) -> None:
        agents = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "custom_work": AgentDrainConfig(chain="chain"),
                "complete": AgentDrainConfig(chain="chain"),
            },
        )
        pipeline = PipelinePolicy(
            phases={
                "custom_work": PhaseDefinition(
                    drain="custom_work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="custom_work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        with pytest.raises(PolicyValidationError, match="no explicit drain_class"):
            validate_drain_contracts(bundle)

    def test_drain_class_present_passes_strict_validation(self) -> None:
        agents = AgentsPolicy(
            forbid_sibling_drain_inference=True,
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={
                "custom_work": AgentDrainConfig(chain="chain", drain_class="development"),
                "complete": AgentDrainConfig(chain="chain", drain_class="development"),
            },
        )
        pipeline = PipelinePolicy(
            phases={
                "custom_work": PhaseDefinition(
                    drain="custom_work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="custom_work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        validate_drain_contracts(bundle)

    def test_legacy_phase_field_requires_commit_rejected(self) -> None:
        with pytest.raises(ValidationError, match="requires_commit has been removed"):
            PhaseDefinition(
                drain="build",
                role="execution",
                requires_commit=True,
                transitions=PhaseTransition(on_success="done"),
            )

    def test_legacy_phase_field_embeds_analysis_rejected(self) -> None:
        with pytest.raises(ValidationError, match="embeds_analysis has been removed"):
            PhaseDefinition(
                drain="build",
                role="execution",
                embeds_analysis=True,
                transitions=PhaseTransition(on_success="done"),
            )


class TestSkipInvocationHasOnSuccess:
    def test_skip_invocation_without_on_success_raises(self) -> None:
        invalid_transitions = PhaseTransition.model_construct(
            on_success=None,
            on_failure=None,
            on_loopback=None,
        )
        work_phase = PhaseDefinition.model_construct(
            drain="work",
            transitions=invalid_transitions,
            role="execution",
            skip_invocation=True,
            bypass_routes={},
            decisions={},
            verification=None,
            parallelization=None,
        )
        policy = PipelinePolicy.model_construct(
            phases={
                "work": work_phase,
                "complete": _strict_skip_invocation_has_on__terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
            loop_counters={},
            budget_counters={},
            post_commit_routes=[],
        )
        bundle = PolicyBundle.model_construct(
            agents=_strict_skip_invocation_has_on__minimal_agents(["work", "complete"]),
            pipeline=policy,
            artifacts=ArtifactsPolicy(artifacts={}),
        )
        with pytest.raises(
            PolicyValidationError,
            match=r"skip_invocation=true requires transitions\.on_success",
        ):
            validate_policy_completeness(bundle)

    def test_skip_invocation_true_with_on_success_set_passes(self) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                skip_invocation=True,
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _strict_skip_invocation_has_on__terminal_phase(),
        }
        bundle = _strict_skip_invocation_has_on__minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)

    def test_skip_invocation_false_without_on_success_does_not_raise_from_this_check(
        self,
    ) -> None:
        phases = {
            "work": PhaseDefinition(
                drain="work",
                role="execution",
                skip_invocation=False,
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": _strict_skip_invocation_has_on__terminal_phase(),
        }
        bundle = _strict_skip_invocation_has_on__minimal_bundle_with_phases(phases)
        validate_policy_completeness(bundle)


class TestValidateChainExists:
    def test_chain_exists(self) -> None:
        bundle = _cached_default_bundle()
        validate_chain_exists("development", bundle)

    def test_chain_not_defined(self) -> None:
        bundle = _cached_default_bundle()
        with pytest.raises(ValueError, match="not defined"):
            validate_chain_exists("nonexistent_chain", bundle)


class TestValidateCheckpointCompatible:
    def test_checkpoint_compatible(self) -> None:
        bundle = _cached_default_bundle()
        validate_checkpoint_compatible("planning", bundle)

    def test_checkpoint_incompatible(self) -> None:
        bundle = _cached_default_bundle()
        with pytest.raises(CheckpointPolicyMismatchError):
            validate_checkpoint_compatible("nonexistent_phase", bundle)


class TestValidateDrainBound:
    def test_drain_bound(self) -> None:
        bundle = _cached_default_bundle()
        validate_drain_bound("planning", bundle)

    @pytest.mark.timeout_seconds(2.0)
    def test_drain_not_bound(self) -> None:
        bundle = _cached_default_bundle()
        with pytest.raises(ValueError, match="not bound"):
            validate_drain_bound("nonexistent_drain", bundle)


class TestValidatePhaseExistsInPolicy:
    def test_phase_exists_in_policy(self) -> None:
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )
        validate_phase_exists_in_policy("development", pipeline)

    def test_phase_not_in_policy(self) -> None:
        pipeline = MagicMock()
        pipeline.phases = {
            "planning": MagicMock(),
            "development": MagicMock(),
            "review": MagicMock(),
        }

        with pytest.raises(CheckpointPolicyMismatchError) as exc_info:
            validate_phase_exists_in_policy("nonexistent_phase", pipeline)
        assert exc_info.value.checkpoint_phase == "nonexistent_phase"


@pytest.mark.timeout_seconds(3.0)
class TestValidatePolicyCompletenessNewRules:
    def _minimal_agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _minimal_analysis_phase(
        self,
        name: DrainName,
        iteration_field: str,
        on_success: str = "complete",
        failure_target: str = "failed",
    ) -> PhaseDefinition:
        return PhaseDefinition(
            drain=name,
            role="analysis",
            transitions=PhaseTransition(
                on_success=on_success,
                on_loopback=name,
            ),
            loop_policy=PhaseLoopPolicy(iteration_state_field=iteration_field),
            decisions={
                "completed": PhaseDecisionRoute(target=on_success, reset_loop=True),
                "failed": PhaseDecisionRoute(target=failure_target, reset_loop=False),
            },
        )

    def _minimal_analysis_artifacts(self, drain: DrainName) -> ArtifactsPolicy:
        return ArtifactsPolicy(
            artifacts={
                "dev_analysis": ArtifactContract(
                    drain=drain,
                    artifact_type="development_analysis_decision",
                    decision_vocabulary=["completed", "failed"],
                )
            }
        )

    def test_uncovered_vocab_entry_raises(self) -> None:
        agents = self._minimal_agents(["development_analysis", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": self._minimal_analysis_phase(
                    "development_analysis", "development_analysis_iteration"
                ),
                "failed": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed", on_loopback="failed"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(
            artifacts={
                "dev_analysis": ArtifactContract(
                    drain="development_analysis",
                    artifact_type="development_analysis_decision",
                    decision_vocabulary=["completed", "rejected"],
                )
            }
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="vocab entry 'rejected' has no route"):
            validate_policy_completeness(bundle)

    def test_uncovered_vocab_with_on_failure_still_raises(self) -> None:
        agents = self._minimal_agents(["development_analysis", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                        on_loopback="development_analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions={
                        "completed": PhaseDecisionRoute(target="complete", reset_loop=True),
                    },
                ),
                "failed": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed", on_loopback="failed"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(
            artifacts={
                "dev_analysis": ArtifactContract(
                    drain="development_analysis",
                    artifact_type="development_analysis_decision",
                    decision_vocabulary=["completed", "rejected"],
                )
            }
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="vocab entry 'rejected' has no route"):
            validate_policy_completeness(bundle)

    def test_commit_phase_without_commit_policy_raises(self) -> None:
        agents = self._minimal_agents(["development_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                ),
                "failed": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed", on_loopback="failed"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_commit",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="role='commit' requires commit_policy"):
            validate_policy_completeness(bundle)

    def test_commit_phase_with_commit_policy_passes(self) -> None:
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": self._minimal_analysis_phase(
                    "development_analysis",
                    "development_analysis_iteration",
                    on_success="development_commit",
                    failure_target="failed_terminal",
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        loop_resets=["development_analysis_iteration"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            loop_counters={"development_analysis_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)

    def test_commit_phase_with_increments_counter_none_is_valid(self) -> None:
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": self._minimal_analysis_phase(
                    "development_analysis",
                    "development_analysis_iteration",
                    on_success="development_commit",
                    failure_target="failed_terminal",
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="none",
                        loop_resets=["development_analysis_iteration"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            loop_counters={"development_analysis_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)

    def test_commit_policy_loop_resets_invalid_field_raises(self) -> None:
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        dev_analysis_decisions = {
            "completed": PhaseDecisionRoute(target="development_commit", reset_loop=True),
            "failed": PhaseDecisionRoute(target="failed_terminal", reset_loop=False),
        }
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development_analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions=dev_analysis_decisions,
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        loop_resets=["nonexistent_iteration_field"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="invalid iteration field"):
            validate_policy_completeness(bundle)

    def test_execution_role_rejects_loop_policy(self) -> None:
        agents = self._minimal_agents(["development", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "development": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                    loop_policy=PhaseLoopPolicy(iteration_state_field="development_iteration"),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            loop_counters={"development_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="development",
            terminal_phase="complete",
        )
        bundle = PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(artifacts={}),
        )

        with pytest.raises(
            PolicyValidationError,
            match="loop_policy is only valid for role='analysis' or role='commit_review'",
        ):
            validate_policy_completeness(bundle)

    def test_commit_policy_loop_resets_valid_field_passes(self) -> None:
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        dev_analysis_decisions = {
            "completed": PhaseDecisionRoute(target="development_commit", reset_loop=True),
            "failed": PhaseDecisionRoute(target="failed_terminal", reset_loop=False),
        }
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development_analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions=dev_analysis_decisions,
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        loop_resets=["development_analysis_iteration"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            loop_counters={"development_analysis_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)

    def test_recovery_terminal_recovery_route_field_rejected(self) -> None:
        with pytest.raises(ValidationError, match="deprecated"):
            RecoveryPolicy.model_validate(
                {
                    "cycle_cap": 200,
                    "terminal_recovery_route": "some_phase",
                    "preserve_session_on_categories": ("agent",),
                }
            )

    def test_recovery_failed_route_unknown_phase_raises_policy_error(self) -> None:
        agents = self._minimal_agents(["planning", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(
                cycle_cap=200,
                failed_route="nonexistent_phase",
                preserve_session_on_categories=("agent",),
            ),
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="nonexistent_phase"):
            validate_policy_completeness(bundle)

    def test_recovery_failed_route_declared_phase_accepted(self) -> None:
        agents = self._minimal_agents(["planning", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(
                cycle_cap=200,
                failed_route="planning",
                preserve_session_on_categories=("agent",),
            ),
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)

    @pytest.mark.parametrize("failed_route", ["phase_failed", "exit_failure"])
    def test_recovery_failed_route_legacy_rejected(self, failed_route: str) -> None:
        with pytest.raises(ValidationError, match="no longer supported"):
            RecoveryPolicy(
                cycle_cap=200,
                failed_route=failed_route,
                preserve_session_on_categories=("agent",),
            )

    def test_review_role_requires_issues_outcome(self) -> None:
        agents = self._minimal_agents(["planning", "review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="review"),
                ),
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="issues_outcome"):
            validate_policy_completeness(bundle)

    def test_review_role_requires_clean_outcome_when_bypass_routes_set(self) -> None:
        agents = self._minimal_agents(["planning", "review", "review_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="review"),
                ),
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    transitions=PhaseTransition(on_success="complete"),
                    bypass_routes={"review_clean": "review_commit"},
                ),
                "review_commit": PhaseDefinition(
                    drain="review_commit",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )
        artifacts = ArtifactsPolicy(artifacts={})
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="clean_outcome"):
            validate_policy_completeness(bundle)

    def test_commit_policy_loop_resets_accepts_commit_review_iteration_field(self) -> None:
        drains = [
            "development_analysis",
            "commit",
            "development_commit_review",
            "development_commit",
            "complete",
        ]
        agents = self._minimal_agents(drains)
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": self._minimal_analysis_phase(
                    "development_analysis",
                    "development_analysis_iteration",
                    on_success="development_commit_review",
                    failure_target="failed_terminal",
                ),
                "development_commit_review": PhaseDefinition(
                    drain="commit",
                    role="commit_review",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development_commit_review",
                        on_failure="failed_terminal",
                    ),
                    loop_policy=PhaseLoopPolicy(iteration_state_field="commit_review_iteration"),
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        loop_resets=["commit_review_iteration"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            loop_counters={
                "development_analysis_iteration": LoopCounterConfig(default_max=3),
                "commit_review_iteration": LoopCounterConfig(default_max=3),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)

    def test_commit_policy_loop_resets_rejects_field_not_from_analysis_or_commit_review(
        self,
    ) -> None:
        agents = self._minimal_agents(["development_analysis", "development_commit", "complete"])
        dev_analysis_decisions = {
            "completed": PhaseDecisionRoute(target="development_commit", reset_loop=True),
            "failed": PhaseDecisionRoute(target="failed_terminal", reset_loop=False),
        }
        pipeline = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development_analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions=dev_analysis_decisions,
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="iteration",
                        loop_resets=["unknown_iteration_field"],
                    ),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
                "complete": PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )
        artifacts = self._minimal_analysis_artifacts("development_analysis")
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        with pytest.raises(PolicyValidationError, match="invalid iteration field"):
            validate_policy_completeness(bundle)


class TestValidatePolicyCompletenessReachability:
    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_linear_chain_all_reachable_passes(self) -> None:
        agents = self._agents(["planning", "development", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)

    def test_orphaned_phase_raises_validation_error(self) -> None:
        agents = self._agents(["planning", "orphan", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "orphan": PhaseDefinition(
                    drain="orphan",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError, match="orphan"):
            validate_policy_completeness(bundle)

    def test_phase_reachable_via_on_failure_passes(self) -> None:
        agents = self._agents(["planning", "fallback", "complete", "crashed"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="fallback",
                    ),
                ),
                "fallback": PhaseDefinition(
                    drain="fallback",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="crashed",
                    ),
                ),
                "complete": self._terminal_phase(),
                "crashed": PhaseDefinition(
                    drain="crashed",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="crashed", on_loopback="crashed"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)

    def test_phase_reachable_via_on_loopback_passes(self) -> None:
        agents = self._agents(["execution", "review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "execution": PhaseDefinition(
                    drain="execution",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="review",
                        on_loopback="execution",
                    ),
                ),
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="execution",
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="execution",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)

    def test_phase_reachable_via_decision_target_passes(self) -> None:
        agents = self._agents(["analysis", "alt_path", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "analysis": PhaseDefinition(
                    drain="analysis",
                    role="analysis",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="analysis",
                    ),
                    loop_policy=PhaseLoopPolicy(
                        iteration_state_field="development_analysis_iteration"
                    ),
                    decisions={
                        "completed": PhaseDecisionRoute(target="complete", reset_loop=True),
                        "needs_work": PhaseDecisionRoute(target="alt_path", reset_loop=False),
                    },
                ),
                "alt_path": PhaseDefinition(
                    drain="alt_path",
                    role="execution",
                    transitions=PhaseTransition(on_success="analysis"),
                ),
                "complete": self._terminal_phase(),
            },
            loop_counters={"development_analysis_iteration": LoopCounterConfig(default_max=3)},
            entry_phase="analysis",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        artifacts = ArtifactsPolicy(
            artifacts={
                "art": ArtifactContract(
                    drain="analysis",
                    artifact_type="development_analysis_decision",
                    decision_vocabulary=["completed", "needs_work"],
                )
            }
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=artifacts)
        validate_policy_completeness(bundle)

    def test_phase_reachable_via_bypass_route_passes(self) -> None:
        agents = self._agents(["review", "shortcut_commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    clean_outcome="review_clean",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="review",
                    ),
                    bypass_routes={"review_clean": "shortcut_commit"},
                ),
                "shortcut_commit": PhaseDefinition(
                    drain="shortcut_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="none",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="review",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)

    def test_multiple_orphaned_phases_all_listed_in_error(self) -> None:
        agents = self._agents(["planning", "orphan_a", "orphan_b", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "orphan_a": PhaseDefinition(
                    drain="orphan_a",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "orphan_b": PhaseDefinition(
                    drain="orphan_b",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="planning",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        error_msg = str(exc_info.value)
        assert "orphan_a" in error_msg
        assert "orphan_b" in error_msg


class TestValidatePolicyCompletenessVerificationRole:
    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_verification_role_requires_verification_block(self) -> None:
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="requires a verification block"):
            validate_policy_completeness(bundle)

    def test_verification_kind_pydantic_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            PhaseVerificationPolicy(**{"kind": "bogus", "gate_for": "advancement"})

    def test_verification_gate_for_pydantic_rejects_invalid(self) -> None:
        with pytest.raises(ValidationError):
            PhaseVerificationPolicy(**{"kind": "none", "gate_for": "unknown_gate"})

    def test_verification_on_failure_route_unknown_phase_rejected(self) -> None:
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="artifact",
                        gate_for="advancement",
                        on_failure_route="nonexistent_phase",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="nonexistent_phase"):
            validate_policy_completeness(bundle)

    def test_verification_on_failure_route_failed_undeclared_rejected(self) -> None:
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="none",
                        gate_for="advancement",
                        on_failure_route="failed",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        with pytest.raises(PolicyValidationError, match="'failed' is not a declared phase"):
            validate_policy_completeness(bundle)

    def test_verification_on_failure_route_legacy_pseudo_rejected(self) -> None:
        for pseudo in ("phase_failed", "exit_failure"):
            agents = self._agents(["verify", "complete"])
            pipeline = PipelinePolicy(
                phases={
                    "verify": PhaseDefinition(
                        drain="verify",
                        role="verification",
                        verification=PhaseVerificationPolicy(
                            kind="none",
                            gate_for="advancement",
                            on_failure_route=pseudo,
                        ),
                        transitions=PhaseTransition(on_success="complete"),
                    ),
                    "complete": self._terminal_phase(),
                },
                entry_phase="verify",
                terminal_phase="complete",
                recovery=RecoveryPolicy(failed_route="complete"),
            )
            bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
            with pytest.raises(PolicyValidationError, match=pseudo):
                validate_policy_completeness(bundle)

    def test_verification_on_failure_route_declared_terminal_phase_accepted(self) -> None:
        agents = self._agents(["verify", "crashed", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="artifact",
                        gate_for="advancement",
                        on_failure_route="crashed",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "crashed": PhaseDefinition(
                    drain="crashed",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="crashed", on_loopback="crashed"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        validate_policy_completeness(bundle)

    def test_verification_with_valid_block_passes(self) -> None:
        agents = self._agents(["verify", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "verify": PhaseDefinition(
                    drain="verify",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="none",
                        gate_for="advancement",
                        on_failure_route=None,
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="verify",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy())
        validate_policy_completeness(bundle)


class TestValidatePostCommitAllBudgetStatesCovered:
    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_success(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def _terminal_failure(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="crashed",
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(on_success="crashed", on_loopback="crashed"),
        )

    def _bundle_with_routes(self, routes: list[tuple[str, str]]) -> PolicyBundle:
        agents = self._agents(["work", "commit", "complete", "crashed"])
        post_commit = [
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="commit", budget_state=state),
                target=target,
            )
            for state, target in routes
        ]
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="crashed",
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_success(),
                "crashed": self._terminal_failure(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True, default_max=5)},
            post_commit_routes=post_commit,
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        return PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )

    @pytest.mark.parametrize(
        ("routes", "missing_token"),
        [
            ([("exhausted", "complete"), ("no_review", "complete")], "remaining"),
            ([("remaining", "work"), ("no_review", "complete")], "exhausted"),
            ([("remaining", "work"), ("exhausted", "complete")], "no_review"),
        ],
    )
    def test_missing_budget_state_raises(
        self, routes: list[tuple[str, str]], missing_token: str
    ) -> None:
        bundle = self._bundle_with_routes(routes)
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert missing_token in str(exc_info.value)

    def test_all_three_budget_states_passes(self) -> None:
        bundle = self._bundle_with_routes(
            [
                ("remaining", "work"),
                ("exhausted", "complete"),
                ("no_review", "complete"),
            ]
        )
        validate_policy_completeness(bundle)


class TestValidatePostCommitRoutesCoverage:
    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_post_commit_routes_required_for_tracked_counter(self) -> None:
        agents = self._agents(["work", "commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True, default_max=5)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        error_msg = str(exc_info.value)
        assert "no post_commit_routes apply to this phase" in error_msg
        assert "commit" in error_msg

    def test_post_commit_routes_present_for_tracked_counter_passes(self) -> None:
        agents = self._agents(["work", "commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True, default_max=5)},
            post_commit_routes=[
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="remaining"),
                    target="work",
                ),
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="exhausted"),
                    target="complete",
                ),
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="no_review"),
                    target="complete",
                ),
            ],
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)

    def test_untracked_counter_does_not_require_post_commit_routes(self) -> None:
        agents = self._agents(["work", "commit", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=False, default_max=0)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)


class TestValidateRequiredInputs:
    def test_missing_prompt_md_raises_with_init_hint(self, tmp_path: Path) -> None:
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_required_inputs(scope)
        msg = str(exc_info.value)
        assert "Required input file not found" in msg
        assert "ralph --init" in msg

    def test_present_prompt_md_does_not_raise(self, tmp_path: Path) -> None:
        (tmp_path / "PROMPT.md").write_text("# Goal\n\nDo something.\n")
        scope = MagicMock()
        scope.root = tmp_path
        validate_required_inputs(scope)

    @pytest.mark.parametrize(
        ("prompt_content", "match_pattern"),
        [
            ("", "empty"),
            (STARTER_PROMPT_SENTINEL + "\n\n# Goal\n\nExample body\n", "starter template"),
            ("# Goal\n\nMy task.\n\n" + STARTER_PROMPT_SENTINEL + "\n", "starter template"),
        ],
    )
    def test_invalid_prompt_md_raises(
        self, tmp_path: Path, prompt_content: str, match_pattern: str
    ) -> None:
        (tmp_path / "PROMPT.md").write_text(prompt_content)
        scope = MagicMock()
        scope.root = tmp_path
        with pytest.raises(PolicyValidationError, match=match_pattern):
            validate_required_inputs(scope)


class TestValidateReviewPhaseOutcomeComplete:
    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_clean_outcome_missing_from_bypass_routes_fails(self) -> None:
        agents = self._agents(["review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    clean_outcome="approved",
                    transitions=PhaseTransition(on_success="complete"),
                    bypass_routes={},
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="review",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError, match=r"clean_outcome.*bypass_routes"):
            validate_policy_completeness(bundle)

    def test_clean_outcome_present_in_bypass_routes_passes(self) -> None:
        agents = self._agents(["review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    clean_outcome="approved",
                    transitions=PhaseTransition(on_success="complete"),
                    bypass_routes={"approved": "complete"},
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="review",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)

    def test_review_phase_without_clean_outcome_skipped(self) -> None:
        agents = self._agents(["review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="review",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)


class TestValidateTerminalFailurePhaseRequired:
    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = {d: AgentDrainConfig(chain=d) for d in drains}
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_success(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def _terminal_failure(self, drain: str = "crashed") -> PhaseDefinition:
        return PhaseDefinition(
            drain=drain,
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(on_success=drain, on_loopback=drain),
        )

    def test_on_failure_without_terminal_failure_phase_raises(self) -> None:
        agents = self._agents(["work", "fallback", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="fallback",
                    ),
                ),
                "fallback": PhaseDefinition(
                    drain="fallback",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_success(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "terminal_outcome='failure'" in str(exc_info.value)

    def test_verification_failure_route_without_terminal_failure_phase_raises(self) -> None:
        agents = self._agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="verification",
                    verification=PhaseVerificationPolicy(
                        kind="artifact",
                        gate_for="advancement",
                        on_failure_route="complete",
                    ),
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_success(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "terminal_outcome='failure'" in str(exc_info.value)

    def test_on_failure_with_terminal_failure_phase_passes(self) -> None:
        agents = self._agents(["work", "fallback", "complete", "crashed"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="fallback",
                    ),
                ),
                "fallback": PhaseDefinition(
                    drain="fallback",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="crashed",
                    ),
                ),
                "complete": self._terminal_success(),
                "crashed": self._terminal_failure(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)

    def test_no_failure_routes_skips_terminal_failure_check(self) -> None:
        agents = self._agents(["work", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_success(),
            },
            entry_phase="work",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)


class TestInvocationGateSuccessPath:
    def _bundle(
        self, *, builder_on_success: str, always_invoke_statuses: list[str] | None = None
    ) -> PolicyBundle:
        agents = AgentsPolicy(
            agent_chains={"chain": AgentChainConfig(agents=["claude"])},
            agent_drains={"drain": AgentDrainConfig(chain="chain")},
        )
        pipeline = PipelinePolicy(
            entry_phase="builder",
            terminal_phase="done",
            phases={
                "builder": PhaseDefinition(
                    drain="drain",
                    role="execution",
                    transitions=PhaseTransition(
                        on_success=builder_on_success,
                        on_failure="failed_terminal",
                    ),
                ),
                "inspector": PhaseDefinition(
                    drain="drain",
                    role="analysis",
                    transitions=PhaseTransition(on_success="done", on_loopback="builder"),
                    loop_policy=PhaseLoopPolicy(iteration_state_field="inspector_iteration"),
                    decisions={
                        "completed": PhaseDecisionRoute(target="done"),
                        "request_changes": PhaseDecisionRoute(target="builder"),
                        "failed": PhaseDecisionRoute(target="failed_terminal"),
                    },
                    invocation_gate=PhaseInvocationGate(
                        upstream_execution_phase="builder",
                        minimum_elapsed_seconds=900.0,
                        always_invoke_statuses=always_invoke_statuses or [],
                    ),
                ),
                "done": PhaseDefinition(
                    drain="done",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(on_success="done"),
                ),
                "failed_terminal": PhaseDefinition(
                    drain="failed_terminal",
                    role="terminal",
                    terminal_outcome="failure",
                    transitions=PhaseTransition(on_success="failed_terminal"),
                ),
            },
            loop_counters={
                "inspector_iteration": LoopCounterConfig(default_max=3),
            },
            recovery=RecoveryPolicy(failed_route="failed_terminal"),
        )
        return PolicyBundle(
            agents=agents,
            pipeline=pipeline,
            artifacts=ArtifactsPolicy(
                artifacts={
                    "inspector_decision": ArtifactContract(
                        drain="drain",
                        artifact_type="development_analysis_decision",
                        decision_vocabulary=["completed", "request_changes", "failed"],
                    ),
                }
            ),
        )

    def test_gate_rejects_upstream_not_on_success_path(self) -> None:
        bundle = self._bundle(builder_on_success="done")
        with pytest.raises(PolicyValidationError, match="must reach .* via on_success"):
            validate_policy_completeness(bundle)

    def test_gate_accepts_upstream_on_success_path(self) -> None:
        bundle = self._bundle(builder_on_success="inspector")
        validate_policy_completeness(bundle)

    def test_gate_accepts_valid_always_invoke_statuses(self) -> None:
        bundle = self._bundle(
            builder_on_success="inspector",
            always_invoke_statuses=["partial", "failed"],
        )
        validate_policy_completeness(bundle)

    def test_gate_rejects_invalid_always_invoke_statuses(self) -> None:
        bundle = self._bundle(
            builder_on_success="inspector",
            always_invoke_statuses=["partial", "bogus"],
        )
        with pytest.raises(PolicyValidationError, match="always_invoke_statuses.*not valid"):
            validate_policy_completeness(bundle)


class TestCommitReviewPolicyValidation:
    def test_default_policy_commit_review_configured(self) -> None:
        bundle = _cached_default_bundle()

        cr = bundle.pipeline.commit_review
        assert cr.max_deleted_files == 50
        assert cr.max_deleted_fraction == 0.25
        assert cr.binary_size_limit == 1_000_000
        assert cr.proposal_retention == 10

        assert "commit_review_rejections" in bundle.pipeline.loop_counters
        rejections_counter = bundle.pipeline.loop_counters["commit_review_rejections"]
        assert rejections_counter.default_max == 2

        for phase_name, phase_def in bundle.pipeline.phases.items():
            if phase_def.commit_policy is not None:
                assert "commit_review_rejections" not in phase_def.commit_policy.loop_resets, (
                    f"phase {phase_name} unexpectedly includes commit_review_rejections in loop_resets"
                )

        for phase_name in ("development_commit_review", "development_final_commit_review"):
            phase = bundle.pipeline.phases[phase_name]
            assert phase.transitions.on_reject == "development"
            assert phase.transitions.on_failure == "failed_terminal"

    def test_commit_review_policy_model_defaults_and_custom(self) -> None:
        default_policy = CommitReviewPolicy()
        assert default_policy.max_deleted_files == 50
        assert default_policy.max_deleted_fraction == 0.25
        assert default_policy.binary_size_limit == 1_000_000
        assert default_policy.proposal_retention == 10

        custom_policy = CommitReviewPolicy(
            max_deleted_files=5,
            max_deleted_fraction=0.5,
            binary_size_limit=10_485_760,
            proposal_retention=20,
        )
        assert custom_policy.max_deleted_files == 5
        assert custom_policy.max_deleted_fraction == 0.5
        assert custom_policy.binary_size_limit == 10_485_760
        assert custom_policy.proposal_retention == 20

    def test_commit_review_policy_validation_constraints(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CommitReviewPolicy(max_deleted_files=-1)
        with pytest.raises(ValidationError):
            CommitReviewPolicy(max_deleted_fraction=-0.1)
        with pytest.raises(ValidationError):
            CommitReviewPolicy(max_deleted_fraction=1.5)
        with pytest.raises(ValidationError):
            CommitReviewPolicy(binary_size_limit=0)
        with pytest.raises(ValidationError):
            CommitReviewPolicy(proposal_retention=0)

    def test_phase_transition_on_reject(self) -> None:
        trans = PhaseTransition(
            on_success="next",
            on_reject="rollback_phase",
            on_failure="fail",
        )
        assert trans.on_reject == "rollback_phase"

    def test_commit_policy_rejects_rejections_counter_in_loop_resets(self) -> None:
        bundle = _cached_default_bundle()
        bad_phase = bundle.pipeline.phases["development_commit"].model_copy(
            update={
                "commit_policy": PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["commit_review_rejections"],
                )
            }
        )
        bad_pipeline = bundle.pipeline.model_copy(
            update={"phases": {**bundle.pipeline.phases, "development_commit": bad_phase}}
        )
        bad_bundle = PolicyBundle(
            agents=bundle.agents,
            pipeline=bad_pipeline,
            artifacts=bundle.artifacts,
        )
        with pytest.raises(PolicyValidationError, match="commit_review_rejections"):
            validate_policy_completeness(bad_bundle)

    def test_group_block_rejects_rejections_counter_in_loop_resets(self) -> None:
        bundle = _cached_default_bundle()
        block = bundle.pipeline.blocks["developer_iteration"]
        bad_block = block.model_copy(
            update={"loop_resets": [*block.loop_resets, "commit_review_rejections"]}
        )
        bad_pipeline = bundle.pipeline.model_copy(
            update={
                "blocks": {**bundle.pipeline.blocks, "developer_iteration": bad_block},
            }
        )
        bad_bundle = bundle.model_copy(update={"pipeline": bad_pipeline})
        with pytest.raises(PolicyValidationError, match="commit_review_rejections"):
            validate_policy_completeness(bad_bundle)

    def test_lifecycle_phase_rejects_rejections_counter_in_loop_resets(self) -> None:
        bundle = _cached_default_bundle()
        phase_name, lifecycle = next(iter(bundle.pipeline.lifecycle_phases.items()))
        bad_lifecycle = lifecycle.model_copy(
            update={"loop_resets": [*lifecycle.loop_resets, "commit_review_rejections"]}
        )
        bad_pipeline = bundle.pipeline.model_copy(
            update={
                "lifecycle_phases": {
                    **bundle.pipeline.lifecycle_phases,
                    phase_name: bad_lifecycle,
                }
            }
        )
        bad_bundle = bundle.model_copy(update={"pipeline": bad_pipeline})
        with pytest.raises(PolicyValidationError, match="commit_review_rejections"):
            validate_policy_completeness(bad_bundle)

    def test_commit_review_phase_rejects_unknown_on_reject_route(self) -> None:
        bundle = _cached_default_bundle()
        bad_trans = bundle.pipeline.phases["development_commit_review"].transitions.model_copy(
            update={"on_reject": "unknown_phase"}
        )
        bad_phase = bundle.pipeline.phases["development_commit_review"].model_copy(
            update={"transitions": bad_trans}
        )
        bad_pipeline = bundle.pipeline.model_copy(
            update={"phases": {**bundle.pipeline.phases, "development_commit_review": bad_phase}}
        )
        bad_bundle = bundle.model_copy(update={"pipeline": bad_pipeline})
        with pytest.raises(PolicyValidationError, match="transitions.on_reject"):
            validate_policy_completeness(bad_bundle)

    def test_commit_review_policy_validation_catches_invalid_fields(self) -> None:
        bundle = _cached_default_bundle()
        bad_cr = CommitReviewPolicy.model_construct(
            max_deleted_files=-5,
            max_deleted_fraction=1.5,
            binary_size_limit=0,
            proposal_retention=0,
        )
        bad_pipeline = bundle.pipeline.model_copy(update={"commit_review": bad_cr})
        bad_bundle = bundle.model_copy(update={"pipeline": bad_pipeline})
        with pytest.raises(PolicyValidationError) as excinfo:
            validate_policy_completeness(bad_bundle)
        msg = str(excinfo.value)
        assert "commit_review.max_deleted_files" in msg
        assert "commit_review.max_deleted_fraction" in msg
        assert "commit_review.binary_size_limit" in msg
        assert "commit_review.proposal_retention" in msg

    def test_commit_review_rejections_counter_must_have_positive_max(self) -> None:
        bundle = _cached_default_bundle()
        bad_counters = {
            **bundle.pipeline.loop_counters,
            "commit_review_rejections": LoopCounterConfig(default_max=0),
        }
        bad_pipeline = bundle.pipeline.model_copy(update={"loop_counters": bad_counters})
        bad_bundle = bundle.model_copy(update={"pipeline": bad_pipeline})
        with pytest.raises(PolicyValidationError, match="loop_counters.commit_review_rejections"):
            validate_policy_completeness(bad_bundle)

    def test_status_bar_phase_abbreviations_for_commit_review(self) -> None:
        from ralph.display.context import make_display_context
        from ralph.display.status_bar import StatusBarModel, render_status_bar

        ctx = make_display_context(force_width=60, env={"NO_COLOR": "1"})
        m1 = StatusBarModel(
            workspace_root="/tmp",
            phase_label="development_commit_review",
            phase_style="",
        )
        assert "DCr" in render_status_bar(m1, ctx=ctx).plain
        m2 = StatusBarModel(
            workspace_root="/tmp",
            phase_label="development_final_commit_review",
            phase_style="",
        )
        assert "FCr" in render_status_bar(m2, ctx=ctx).plain


class TestPhaseAllowedDirectories:
    PHASE_HEADER = '[blocks.development_commit_review.phase]\ndrain = "commit"\n'

    @classmethod
    def _config_dir_with_allowed_directories(cls, tmp_path: Path, entries: str) -> Path:
        defaults_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        config_dir = tmp_path / ".agent"
        config_dir.mkdir(parents=True)
        for filename in ("agents.toml", "pipeline.toml", "artifacts.toml"):
            shutil.copy(defaults_dir / filename, config_dir / filename)
        pipeline_path = config_dir / "pipeline.toml"
        source = pipeline_path.read_text()
        assert cls.PHASE_HEADER in source, "bundled default pipeline.toml phase header changed"
        pipeline_path.write_text(
            source.replace(
                cls.PHASE_HEADER,
                f"{cls.PHASE_HEADER}allowed_directories = [{entries}]\n",
            )
        )
        return config_dir

    def test_default_policy_leaves_allowed_directories_unset(self) -> None:
        bundle = _cached_default_bundle()

        assert bundle.pipeline.phases["development_commit_review"].allowed_directories == []
        assert bundle.pipeline.phases["development_final_commit_review"].allowed_directories == []

    def test_pipeline_toml_parses_phase_allowed_directories(self, tmp_path: Path) -> None:
        config_dir = self._config_dir_with_allowed_directories(tmp_path, "'src', 'docs'")

        bundle = load_policy(config_dir)

        phase_def = bundle.pipeline.phases["development_commit_review"]
        assert phase_def.allowed_directories == ["src", "docs"]

    @pytest.mark.parametrize(
        "entry",
        ["", " ", "/absolute/path", "..", "src/../../etc", "src\\windows"],
    )
    def test_invalid_allowed_directories_entries_are_rejected(self, entry: str) -> None:
        with pytest.raises(ValidationError, match="allowed_directories"):
            PhaseDefinition(
                drain="commit",
                role="commit_review",
                allowed_directories=[entry],
                transitions=PhaseTransition(on_success="done"),
            )

    def test_invalid_allowed_directories_entry_fails_the_policy_load(self, tmp_path: Path) -> None:
        config_dir = self._config_dir_with_allowed_directories(tmp_path, "'../outside'")

        with pytest.raises((LoaderPolicyValidationError, ValidationError)) as excinfo:
            load_policy(config_dir, strict_optional_files=True)

        assert "allowed_directories" in str(excinfo.value)

    def test_allowed_directories_reaches_out_of_scope_tripwire(self, tmp_path: Path) -> None:
        config_dir = self._config_dir_with_allowed_directories(tmp_path, "'src'")
        bundle = load_policy(config_dir)
        phase_def = bundle.pipeline.phases["development_commit_review"]

        outside = evaluate_tripwires(
            [
                DiffEntry(
                    path="scripts/deploy.sh",
                    status="M",
                    old_mode="100755",
                    new_mode="100755",
                    size=12,
                    is_binary=False,
                )
            ],
            tracked_count=3,
            thresholds=TripwireThresholds(),
            allowed_dirs=phase_def.allowed_directories,
        )
        assert [finding.category for finding in outside.soft] == ["out_of_scope"]
        assert outside.soft[0].path == "scripts/deploy.sh"

        inside = evaluate_tripwires(
            [
                DiffEntry(
                    path="src/app.py",
                    status="M",
                    old_mode="100644",
                    new_mode="100644",
                    size=12,
                    is_binary=False,
                )
            ],
            tracked_count=3,
            thresholds=TripwireThresholds(),
            allowed_dirs=phase_def.allowed_directories,
        )
        assert [finding.category for finding in inside.soft] == []
