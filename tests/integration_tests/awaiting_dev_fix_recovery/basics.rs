//! Basic reducer/orchestration expectations for `AwaitingDevFix`.

use crate::common::with_locked_prompt_permissions;
use crate::test_timeout::with_default_timeout;
use ralph_workflow::agents::session::AuditTrail;
use ralph_workflow::agents::AgentRole;
use ralph_workflow::config::CloudConfig;
use ralph_workflow::reducer::determine_next_effect;
use ralph_workflow::reducer::effect::Effect;
use ralph_workflow::reducer::event::{AwaitingDevFixEvent, PipelineEvent, PipelinePhase};
use ralph_workflow::reducer::state::PipelineState;
use ralph_workflow::reducer::state_reduction::reduce;

/// Test that pipeline transitions to `AwaitingDevFix` on failure.
#[test]
fn transitions_to_awaiting_dev_fix_on_failure() {
    with_default_timeout(|| {
        use ralph_workflow::reducer::event::{ErrorEvent, PromptInputEvent};

        let state = PipelineState::initial(1, 0);

        let error_event = PipelineEvent::PromptInput(PromptInputEvent::HandlerError {
            phase: PipelinePhase::Planning,
            error: ErrorEvent::AgentChainExhausted {
                role: AgentRole::Developer,
                phase: PipelinePhase::Planning,
                cycle: 3,
            },
        });

        let new_state = reduce(state, error_event);

        assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
        assert_eq!(new_state.previous_phase, Some(PipelinePhase::Planning));
    });
}

/// Test that `TriggerDevFixFlow` effect is determined for `AwaitingDevFix` phase.
#[test]
fn awaiting_dev_fix_derives_trigger_dev_fix_flow() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::AwaitingDevFix;
        state.previous_phase = Some(PipelinePhase::Planning);

        let effect = determine_next_effect(&state);
        assert!(
            matches!(effect, Effect::TriggerDevFixFlow { .. }),
            "AwaitingDevFix should determine TriggerDevFixFlow effect, got {effect:?}"
        );
    });
}

/// `CompletionMarkerEmitted` transitions `AwaitingDevFix` -> Interrupted.
#[test]
fn completion_marker_emitted_transitions_to_interrupted() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::CompletionMarkerEmitted {
            is_failure: true,
        });
        let new_state = reduce(state, event);

        assert_eq!(new_state.phase, PipelinePhase::Interrupted);
    });
}

/// Test that `DevFixAgentUnavailable` does not terminate immediately.
#[test]
fn dev_fix_agent_unavailable_does_not_interrupt() {
    with_default_timeout(|| {
        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.previous_phase = Some(PipelinePhase::Planning);

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixAgentUnavailable {
            failed_phase: PipelinePhase::Planning,
            reason: "usage limit exceeded".to_string(),
        });
        let new_state = reduce(state, event);

        assert_eq!(new_state.phase, PipelinePhase::AwaitingDevFix);
    });
}

/// Test that successful recovery clears recovery state.
#[test]
fn recovery_succeeded_clears_recovery_tracking() {
    with_default_timeout(|| {
        let mut state = with_locked_prompt_permissions(PipelineState::initial(1, 0));
        state.phase = PipelinePhase::Development;
        state.dev_fix_attempt_count = 2;
        state.recovery_escalation_level = 1;
        state.failed_phase_for_recovery = Some(PipelinePhase::Development);

        let event = PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoverySucceeded {
            level: 1,
            total_attempts: 2,
        });
        state = reduce(state, event);

        assert_eq!(state.dev_fix_attempt_count, 0);
        assert_eq!(state.recovery_escalation_level, 0);
        assert_eq!(state.failed_phase_for_recovery, None);
    });
}

#[test]
fn attempt_recovery_uses_previous_phase_when_failed_phase_for_recovery_missing() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRegistry;
        use ralph_workflow::checkpoint::{ExecutionHistory, RunContext};
        use ralph_workflow::config::Config;
        use ralph_workflow::executor::MockProcessExecutor;
        use ralph_workflow::logger::{Colors, Logger};
        use ralph_workflow::pipeline::Timer;
        use ralph_workflow::prompts::template_context::TemplateContext;
        use ralph_workflow::reducer::effect::{Effect, EffectHandler};
        use ralph_workflow::reducer::event::{AwaitingDevFixEvent, PipelineEvent};
        use ralph_workflow::reducer::handler::MainEffectHandler;
        use ralph_workflow::workspace::MemoryWorkspace;
        use std::path::PathBuf;
        use std::sync::Arc;

        let config = Config::default();
        let colors = Colors::new();
        let logger = Logger::new(colors);
        let mut timer = Timer::new();
        let template_context = TemplateContext::default();
        let registry = AgentRegistry::new().unwrap();
        let executor = Arc::new(MockProcessExecutor::new());

        let repo_root = PathBuf::from("/test/repo");
        let workspace = MemoryWorkspace::new(repo_root.clone());
        let run_log_context = ralph_workflow::logging::RunLogContext::new(&workspace)
            .expect("Failed to create run log context");
        let cloud_config = CloudConfig::disabled();
        let git_env = ralph_workflow::runtime::environment::mock::MockGitEnvironment::new();

        let mut ctx = ralph_workflow::phases::PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "test-developer",
            reviewer_agent: "test-reviewer",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: &*executor,
            executor_arc: Arc::clone(&executor)
                as Arc<dyn ralph_workflow::executor::ProcessExecutor>,
            repo_root: &repo_root,
            workspace: &workspace,
            workspace_arc: Arc::new(workspace.clone())
                as Arc<dyn ralph_workflow::workspace::Workspace>,
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud_config,
            env: &git_env,
            active_session: None,
            audit_trail: AuditTrail::new(),
        };

        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.previous_phase = Some(PipelinePhase::Review);
        state.failed_phase_for_recovery = None;

        let mut handler = MainEffectHandler::new(state);
        let res = handler
            .execute(
                Effect::AttemptRecovery {
                    level: 1,
                    attempt_count: 1,
                },
                &mut ctx,
            )
            .expect("effect execution should succeed");

        assert!(matches!(
            res.event,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                level: 1,
                attempt_count: 1,
                target_phase: PipelinePhase::Review
            })
        ));
    });
}

#[test]
fn attempt_recovery_never_targets_awaiting_dev_fix() {
    with_default_timeout(|| {
        use ralph_workflow::agents::AgentRegistry;
        use ralph_workflow::checkpoint::{ExecutionHistory, RunContext};
        use ralph_workflow::config::Config;
        use ralph_workflow::executor::MockProcessExecutor;
        use ralph_workflow::logger::{Colors, Logger};
        use ralph_workflow::pipeline::Timer;
        use ralph_workflow::prompts::template_context::TemplateContext;
        use ralph_workflow::reducer::effect::{Effect, EffectHandler};
        use ralph_workflow::reducer::event::{AwaitingDevFixEvent, PipelineEvent};
        use ralph_workflow::reducer::handler::MainEffectHandler;
        use ralph_workflow::workspace::MemoryWorkspace;
        use std::path::PathBuf;
        use std::sync::Arc;

        let config = Config::default();
        let colors = Colors::new();
        let logger = Logger::new(colors);
        let mut timer = Timer::new();
        let template_context = TemplateContext::default();
        let registry = AgentRegistry::new().unwrap();
        let executor = Arc::new(MockProcessExecutor::new());

        let repo_root = PathBuf::from("/test/repo");
        let workspace = MemoryWorkspace::new(repo_root.clone());
        let run_log_context = ralph_workflow::logging::RunLogContext::new(&workspace)
            .expect("Failed to create run log context");
        let cloud_config = CloudConfig::disabled();
        let git_env = ralph_workflow::runtime::environment::mock::MockGitEnvironment::new();

        let mut ctx = ralph_workflow::phases::PhaseContext {
            config: &config,
            registry: &registry,
            logger: &logger,
            colors: &colors,
            timer: &mut timer,
            developer_agent: "test-developer",
            reviewer_agent: "test-reviewer",
            review_guidelines: None,
            template_context: &template_context,
            run_context: RunContext::new(),
            execution_history: ExecutionHistory::new(),
            executor: &*executor,
            executor_arc: Arc::clone(&executor)
                as Arc<dyn ralph_workflow::executor::ProcessExecutor>,
            repo_root: &repo_root,
            workspace: &workspace,
            workspace_arc: Arc::new(workspace.clone())
                as Arc<dyn ralph_workflow::workspace::Workspace>,
            run_log_context: &run_log_context,
            cloud_reporter: None,
            cloud: &cloud_config,
            env: &git_env,
            active_session: None,
            audit_trail: AuditTrail::new(),
        };

        let mut state = PipelineState::initial(1, 0);
        state.phase = PipelinePhase::AwaitingDevFix;
        state.previous_phase = Some(PipelinePhase::AwaitingDevFix);
        state.failed_phase_for_recovery = Some(PipelinePhase::AwaitingDevFix);

        let mut handler = MainEffectHandler::new(state);
        let res = handler
            .execute(
                Effect::AttemptRecovery {
                    level: 1,
                    attempt_count: 1,
                },
                &mut ctx,
            )
            .expect("effect execution should succeed");

        assert!(matches!(
            res.event,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::RecoveryAttempted {
                target_phase: PipelinePhase::Development,
                ..
            })
        ));
    });
}
