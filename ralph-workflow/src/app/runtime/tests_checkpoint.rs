use anyhow::Result;

use super::{run_event_loop_with_handler, EventLoopConfig};
use crate::phases::PhaseContext;
use crate::reducer::PipelineState;

#[test]
fn test_event_loop_does_not_bypass_save_checkpoint_when_checkpointing_disabled() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
    use crate::reducer::event::{PipelineEvent, PipelinePhase};
    use crate::reducer::state::PromptPermissionsState;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    #[derive(Debug)]
    struct TestHandler {
        state: PipelineState,
        saw_save_checkpoint: bool,
    }

    impl TestHandler {
        fn new(state: PipelineState) -> Self {
            Self {
                state,
                saw_save_checkpoint: false,
            }
        }
    }

    impl EffectHandler<'_> for TestHandler {
        fn execute(&mut self, effect: Effect, _ctx: &mut PhaseContext<'_>) -> Result<EffectResult> {
            match effect {
                Effect::SaveCheckpoint { trigger } => {
                    self.saw_save_checkpoint = true;
                    Ok(EffectResult::event(PipelineEvent::checkpoint_saved(
                        trigger,
                    )))
                }
                unexpected => panic!("unexpected effect: {unexpected:?}"),
            }
        }
    }

    impl super::StatefulHandler for TestHandler {
        fn update_state(&mut self, state: PipelineState) {
            self.state = state;
        }
    }

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

    let mut ctx = PhaseContext {
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
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    // Construct a terminal state that deterministically derives SaveCheckpoint.
    // Interrupted from AwaitingDevFix with no checkpoint saved must execute SaveCheckpoint
    // even if checkpointing is disabled.
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        previous_phase: Some(PipelinePhase::AwaitingDevFix),
        checkpoint_saved_count: 0,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: false,
            restored: true,
            last_warning: None,
        },
        ..PipelineState::initial(1, 0)
    };
    let mut handler = TestHandler::new(state);

    let loop_config = EventLoopConfig { max_iterations: 10 };

    let result = run_event_loop_with_handler(
        &mut ctx,
        Some(handler.state.clone()),
        loop_config,
        &mut handler,
    )
    .expect("event loop should run");

    assert!(
        handler.saw_save_checkpoint,
        "expected SaveCheckpoint to be executed through the handler"
    );
    assert!(
        result.completed,
        "expected pipeline to complete after SaveCheckpoint"
    );
}

#[test]
fn test_event_loop_result_completed_true_for_interrupted_with_checkpoint() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
    use crate::reducer::event::PipelinePhase;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    #[derive(Debug)]
    struct PanicHandler;

    impl EffectHandler<'_> for PanicHandler {
        fn execute(
            &mut self,
            _effect: Effect,
            _ctx: &mut PhaseContext<'_>,
        ) -> Result<EffectResult> {
            panic!("event loop should not execute effects when initial state is terminal");
        }
    }

    impl super::StatefulHandler for PanicHandler {
        fn update_state(&mut self, _state: PipelineState) {}
    }

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

    let mut ctx = PhaseContext {
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
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        checkpoint_saved_count: 1,
        ..PipelineState::initial(0, 0)
    };

    let mut handler = PanicHandler;
    let loop_config = EventLoopConfig { max_iterations: 10 };

    let result =
        run_event_loop_with_handler(&mut ctx, Some(state.clone()), loop_config, &mut handler)
            .expect("event loop should run");

    assert!(
        result.completed,
        "Interrupted+checkpoint is terminal and should be reported as completed (matches state.is_complete())"
    );
    assert_eq!(
        result.final_phase,
        PipelinePhase::Interrupted,
        "event loop should report the final phase"
    );
    assert!(
        state.is_complete(),
        "State.is_complete() should return true for Interrupted+checkpoint"
    );
    assert_eq!(result.events_processed, 0);
}

#[test]
fn test_event_loop_routes_handler_panic_through_awaiting_dev_fix_and_completes() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
    use crate::workspace::MemoryWorkspace;
    use crate::workspace::Workspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    #[derive(Debug)]
    struct PanickingHandler;

    impl EffectHandler<'_> for PanickingHandler {
        fn execute(
            &mut self,
            _effect: Effect,
            _ctx: &mut PhaseContext<'_>,
        ) -> Result<EffectResult> {
            panic!("boom")
        }
    }

    impl super::StatefulHandler for PanickingHandler {
        fn update_state(&mut self, _state: PipelineState) {}
    }

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();

    let mut ctx = PhaseContext {
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
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    // The interrupt flags are process-global; coordinate all test access so
    // parallel tests can't steal each other's pending interrupt requests.
    let _lock = crate::interrupt::interrupt_test_lock();

    // Guarantee clean state.
    let _ = crate::interrupt::take_user_interrupt_request();
    crate::interrupt::reset_user_interrupted_occurred();

    let state = PipelineState::initial(0, 0);
    let mut handler = PanickingHandler;
    let loop_config = EventLoopConfig { max_iterations: 1 };

    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
        .expect("event loop should return an EventLoopResult even on panic");
    assert!(
        !result.completed,
        "expected handler panic to be reported as incomplete"
    );
    assert!(workspace.exists(&run_log_context.event_loop_trace()));
    assert!(workspace.exists(std::path::Path::new(".agent/tmp/completion_marker")));
}

#[test]
fn test_max_iterations_in_awaiting_dev_fix_runs_save_checkpoint_effect() {
    use crate::agents::{AgentRegistry, AgentRole};
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
    use crate::reducer::event::{AwaitingDevFixEvent, PipelinePhase};
    use crate::reducer::PipelineEvent;
    use crate::workspace::{MemoryWorkspace, Workspace};
    use std::path::{Path, PathBuf};
    use std::sync::Arc;

    #[derive(Debug)]
    struct StallingHandler {
        state: PipelineState,
        effects: Vec<Effect>,
    }

    impl StallingHandler {
        fn new(state: PipelineState) -> Self {
            Self {
                state,
                effects: Vec::new(),
            }
        }
    }

    impl EffectHandler<'_> for StallingHandler {
        fn execute(&mut self, effect: Effect, _ctx: &mut PhaseContext<'_>) -> Result<EffectResult> {
            self.effects.push(effect.clone());
            let event = match effect {
                Effect::SaveCheckpoint { trigger } => PipelineEvent::checkpoint_saved(trigger),
                _ => PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixTriggered {
                    failed_phase: PipelinePhase::Development,
                    failed_role: AgentRole::Developer,
                }),
            };
            Ok(EffectResult::event(event))
        }
    }

    impl super::StatefulHandler for StallingHandler {
        fn update_state(&mut self, state: PipelineState) {
            self.state = state;
        }
    }

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

    let mut ctx = PhaseContext {
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
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    // The interrupt flags are process-global; coordinate all test access so
    // parallel tests can't steal each other's pending interrupt requests.
    let _lock = crate::interrupt::interrupt_test_lock();

    // Guarantee clean state: a parallel test (e.g., the stdout_cancel_watcher test)
    // may hold the global interrupt flag set. Without this drain, the event loop would
    // short-circuit to Interrupted instead of testing the max-iterations AwaitingDevFix path.
    let _ = crate::interrupt::take_user_interrupt_request();
    crate::interrupt::reset_user_interrupted_occurred();

    let mut state = PipelineState::initial(1, 1);
    state.phase = PipelinePhase::AwaitingDevFix;
    state.previous_phase = Some(PipelinePhase::Development);

    let mut handler = StallingHandler::new(state.clone());
    let loop_config = EventLoopConfig { max_iterations: 2 };

    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
        .expect("event loop should run");

    assert!(
        result.completed,
        "expected forced completion when max iterations reached in AwaitingDevFix"
    );
    assert!(
        handler
            .effects
            .iter()
            .any(|effect| matches!(effect, Effect::SaveCheckpoint { .. })),
        "forced completion should execute SaveCheckpoint instead of mutating state directly"
    );
    assert!(
        workspace.exists(Path::new(".agent/tmp/completion_marker")),
        "completion marker should be written when max iterations are exceeded"
    );
}

#[test]
fn test_max_iterations_after_completion_marker_runs_save_checkpoint() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
    use crate::reducer::event::{AwaitingDevFixEvent, PipelinePhase};
    use crate::reducer::PipelineEvent;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    #[derive(Debug)]
    struct BoundaryHandler {
        state: PipelineState,
        effects: Vec<Effect>,
    }

    impl BoundaryHandler {
        fn new(state: PipelineState) -> Self {
            Self {
                state,
                effects: Vec::new(),
            }
        }
    }

    impl EffectHandler<'_> for BoundaryHandler {
        fn execute(&mut self, effect: Effect, _ctx: &mut PhaseContext<'_>) -> Result<EffectResult> {
            self.effects.push(effect.clone());
            match effect {
                Effect::TriggerDevFixFlow {
                    failed_phase,
                    failed_role,
                    ..
                } => {
                    let mut result = EffectResult::event(PipelineEvent::AwaitingDevFix(
                        AwaitingDevFixEvent::DevFixTriggered {
                            failed_phase,
                            failed_role,
                        },
                    ));
                    result = result.with_additional_event(PipelineEvent::AwaitingDevFix(
                        AwaitingDevFixEvent::DevFixCompleted {
                            success: false,
                            summary: None,
                        },
                    ));
                    result = result.with_additional_event(PipelineEvent::AwaitingDevFix(
                        AwaitingDevFixEvent::CompletionMarkerEmitted { is_failure: true },
                    ));
                    Ok(result)
                }
                Effect::SaveCheckpoint { trigger } => Ok(EffectResult::event(
                    PipelineEvent::checkpoint_saved(trigger),
                )),
                _ => panic!("Unexpected effect: {effect:?}"),
            }
        }
    }

    impl super::StatefulHandler for BoundaryHandler {
        fn update_state(&mut self, state: PipelineState) {
            self.state = state;
        }
    }

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

    let mut ctx = PhaseContext {
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
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    // The interrupt flags are process-global; coordinate all test access so
    // parallel tests can't steal each other's pending interrupt requests.
    let _lock = crate::interrupt::interrupt_test_lock();

    // Guarantee clean state so that the AwaitingDevFix→Interrupted transition under test
    // is not short-circuited.
    let _ = crate::interrupt::take_user_interrupt_request();
    crate::interrupt::reset_user_interrupted_occurred();

    let state = PipelineState {
        phase: PipelinePhase::AwaitingDevFix,
        previous_phase: Some(PipelinePhase::Development),
        ..PipelineState::initial(1, 1)
    };

    let mut handler = BoundaryHandler::new(state.clone());
    let loop_config = EventLoopConfig { max_iterations: 3 };

    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
        .expect("event loop should run");

    assert!(
        result.completed,
        "expected completion after AwaitingDevFix transitions to Interrupted"
    );
    assert!(
        handler
            .effects
            .iter()
            .any(|effect| matches!(effect, Effect::SaveCheckpoint { .. })),
        "SaveCheckpoint should run even when max iterations reached after completion marker"
    );
}

#[test]
fn test_create_initial_state_with_config_plumbs_max_same_agent_retry_count() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let cloud = crate::config::types::CloudConfig::disabled();

    let config = Config {
        max_same_agent_retries: Some(5),
        ..Default::default()
    };

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

    let ctx = PhaseContext {
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
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    let state = super::create_initial_state_with_config(&ctx);
    assert_eq!(state.continuation.max_same_agent_retry_count, 5);
}

#[test]
fn test_create_initial_state_with_config_plumbs_commit_residual_retry_budget() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let cloud = crate::config::types::CloudConfig::disabled();

    let config = Config {
        max_commit_residual_retries: Some(4),
        ..Default::default()
    };

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

    let ctx = PhaseContext {
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
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    let state = super::create_initial_state_with_config(&ctx);
    assert_eq!(state.max_commit_residual_retries, 4);
}

#[test]
fn test_event_loop_honors_user_interrupt_by_transitioning_to_interrupted_and_checkpointing() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
    use crate::reducer::event::{CheckpointTrigger, PipelineEvent, PipelinePhase};
    use crate::reducer::state::PromptPermissionsState;
    use crate::reducer::PipelineState;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    #[derive(Debug)]
    struct TestHandler {
        state: PipelineState,
        effects: Vec<Effect>,
    }

    impl TestHandler {
        fn new(state: PipelineState) -> Self {
            Self {
                state,
                effects: Vec::new(),
            }
        }
    }

    impl EffectHandler<'_> for TestHandler {
        fn execute(
            &mut self,
            effect: Effect,
            _ctx: &mut PhaseContext<'_>,
        ) -> anyhow::Result<EffectResult> {
            self.effects.push(effect.clone());
            match effect {
                Effect::RestorePromptPermissions => Ok(EffectResult::event(
                    PipelineEvent::prompt_permissions_restored(),
                )),
                Effect::SaveCheckpoint { trigger } => Ok(EffectResult::event(
                    PipelineEvent::checkpoint_saved(trigger),
                )),
                unexpected => {
                    panic!("unexpected effect during user interrupt handling: {unexpected:?}")
                }
            }
        }
    }

    impl super::StatefulHandler for TestHandler {
        fn update_state(&mut self, state: PipelineState) {
            self.state = state;
        }
    }

    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let cloud = crate::config::types::CloudConfig::disabled();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

    let mut ctx = PhaseContext {
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
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    // Start from a post-interrupt state produced by reducer handling of Ctrl+C.
    // We construct it directly to avoid racing on the process-global interrupt flag.
    // This test verifies post-interrupt orchestration (permissions restore + checkpoint).
    let state = PipelineState {
        phase: PipelinePhase::Interrupted,
        interrupted_by_user: true,
        previous_phase: Some(PipelinePhase::Planning),
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(1, 0)
    };
    let mut handler = TestHandler::new(state.clone());

    let loop_config = EventLoopConfig { max_iterations: 10 };
    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
        .expect("event loop should run");

    assert!(
        result.completed,
        "expected event loop to complete after user interrupt handling"
    );
    assert_eq!(
        result.final_phase,
        PipelinePhase::Interrupted,
        "expected user interrupt to transition pipeline to Interrupted"
    );
    assert!(
        result.final_state.interrupted_by_user,
        "expected interrupted_by_user=true for Ctrl+C interruption"
    );

    assert!(
        handler
            .effects
            .iter()
            .any(|e| matches!(e, Effect::RestorePromptPermissions)),
        "expected RestorePromptPermissions to execute before checkpoint"
    );
    assert!(
        handler.effects.iter().any(|e| matches!(
            e,
            Effect::SaveCheckpoint {
                trigger: CheckpointTrigger::Interrupt
            }
        )),
        "expected SaveCheckpoint with Interrupt trigger for Ctrl+C interruption"
    );
}

/// TDD test: `run_event_loop_with_handler` should accept a generic `EffectHandler`
/// allowing `MockEffectHandler` to be injected for testing.
#[cfg(feature = "test-utils")]
#[test]
fn test_run_event_loop_with_mock_handler() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::mock_effect_handler::MockEffectHandler;
    use crate::reducer::PipelineState;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let cloud = crate::config::types::CloudConfig::disabled();

    // Create test fixtures
    let config = Config::default();
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();

    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();

    // Create PhaseContext
    let mut ctx = PhaseContext {
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
        executor_arc: Arc::clone(&executor) as Arc<dyn crate::executor::ProcessExecutor>,
        repo_root: &repo_root,
        workspace: &workspace,
        workspace_arc: std::sync::Arc::new(workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    // Create mock handler
    let state = PipelineState::initial(1, 0);
    let mut handler = MockEffectHandler::new(state.clone());

    let loop_config = EventLoopConfig {
        max_iterations: 100,
    };

    // This should compile and run with the mock handler
    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler);

    assert!(result.is_ok(), "Event loop should complete successfully");

    // Mock handler should have captured effects
    assert!(
        handler.effect_count() > 0,
        "Mock handler should have captured at least one effect"
    );
}
