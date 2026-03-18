use super::trace::{EventTraceBuffer, EventTraceEntry};
use super::{run_event_loop_with_handler, EventLoopConfig};

#[test]
fn test_event_loop_includes_review_when_reviewer_reviews_nonzero() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::Effect;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::mock_effect_handler::MockEffectHandler;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config {
        developer_iters: 1,
        reviewer_reviews: 1,
        ..Config::default()
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

    let state = super::create_initial_state_with_config(&ctx);
    let mut handler = MockEffectHandler::new(state.clone());
    let loop_config = EventLoopConfig {
        max_iterations: 500,
    };

    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
        .expect("event loop should run");
    assert!(result.completed, "expected pipeline to complete");
    assert_eq!(handler.state.phase, PipelinePhase::Complete);

    let effects = handler.captured_effects();
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::PrepareReviewContext { .. })),
        "expected review to run when reviewer_reviews>0"
    );
}

#[test]
fn test_event_loop_skips_review_when_reviewer_reviews_zero_but_still_commits_dev_iteration() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::Effect;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::mock_effect_handler::MockEffectHandler;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config {
        developer_iters: 1,
        reviewer_reviews: 0,
        ..Config::default()
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

    let state = super::create_initial_state_with_config(&ctx);
    let mut handler = MockEffectHandler::new(state.clone());
    let loop_config = EventLoopConfig {
        max_iterations: 500,
    };

    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
        .expect("event loop should run");
    assert!(result.completed, "expected pipeline to complete");
    assert_eq!(handler.state.phase, PipelinePhase::Complete);

    let effects = handler.captured_effects();
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::PrepareDevelopmentContext { .. })),
        "expected development chain to run when developer_iters>0"
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CreateCommit { .. })),
        "expected commit to be created for dev iteration"
    );
    assert!(
        !effects
            .iter()
            .any(|e| matches!(e, Effect::PrepareReviewContext { .. })),
        "expected review to be skipped when reviewer_reviews=0"
    );
}

#[test]
fn test_event_loop_effect_order_dev_then_commit_then_review_then_complete() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::Effect;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::mock_effect_handler::MockEffectHandler;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    fn idx(effects: &[Effect], pred: impl Fn(&Effect) -> bool) -> Option<usize> {
        effects.iter().position(pred)
    }

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config {
        developer_iters: 1,
        reviewer_reviews: 1,
        ..Config::default()
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

    let state = super::create_initial_state_with_config(&ctx);
    let mut handler = MockEffectHandler::new(state.clone());
    let loop_config = EventLoopConfig {
        max_iterations: 500,
    };

    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
        .expect("event loop should run");
    assert!(result.completed, "expected pipeline to complete");
    assert_eq!(handler.state.phase, PipelinePhase::Complete);

    let effects = handler.captured_effects();

    let dev_idx = idx(&effects, |e| {
        matches!(e, Effect::ApplyDevelopmentOutcome { .. })
    })
    .expect("expected development outcome effect");
    let commit_idx = idx(&effects, |e| matches!(e, Effect::CreateCommit { .. }))
        .expect("expected commit creation effect");
    let review_ctx_idx = idx(&effects, |e| {
        matches!(e, Effect::PrepareReviewContext { .. })
    })
    .expect("expected review context preparation effect");
    let restore_idx = idx(&effects, |e| matches!(e, Effect::RestorePromptPermissions))
        .expect("expected restore prompt permissions effect");

    assert!(
        dev_idx < commit_idx,
        "expected development to occur before commit (dev_idx={dev_idx}, commit_idx={commit_idx})"
    );
    assert!(
            commit_idx < review_ctx_idx,
            "expected commit to occur before review (commit_idx={commit_idx}, review_ctx_idx={review_ctx_idx})"
        );
    assert!(
            review_ctx_idx < restore_idx,
            "expected review to occur before finalizing/complete (review_ctx_idx={review_ctx_idx}, restore_idx={restore_idx})"
        );
}

#[test]
fn test_event_loop_skips_planning_and_development_when_developer_iters_zero() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::Effect;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::mock_effect_handler::MockEffectHandler;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config {
        developer_iters: 0,
        reviewer_reviews: 1,
        ..Config::default()
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

    let state = super::create_initial_state_with_config(&ctx);
    let mut handler = MockEffectHandler::new(state.clone());
    let loop_config = EventLoopConfig {
        max_iterations: 500,
    };

    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
        .expect("event loop should run");
    assert!(result.completed, "expected pipeline to complete");
    assert_eq!(handler.state.phase, PipelinePhase::Complete);

    let effects = handler.captured_effects();
    assert!(
        !effects.iter().any(|e| matches!(
            e,
            Effect::PreparePlanningPrompt { .. }
                | Effect::InvokePlanningAgent { .. }
                | Effect::ExtractPlanningXml { .. }
                | Effect::ValidatePlanningXml { .. }
                | Effect::WritePlanningMarkdown { .. }
                | Effect::ArchivePlanningXml { .. }
                | Effect::ApplyPlanningOutcome { .. }
                | Effect::PrepareDevelopmentContext { .. }
                | Effect::PrepareDevelopmentPrompt { .. }
                | Effect::InvokeDevelopmentAgent { .. }
                | Effect::ExtractDevelopmentXml { .. }
                | Effect::ValidateDevelopmentXml { .. }
                | Effect::ApplyDevelopmentOutcome { .. }
                | Effect::ArchiveDevelopmentXml { .. }
        )),
        "expected no planning/development effects when developer_iters=0"
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::PrepareReviewContext { .. })),
        "expected review effects when reviewer_reviews>0"
    );
}

#[test]
fn test_event_loop_reviews_and_commits_when_developer_iters_zero_and_reviewer_reviews_nonzero() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::Effect;
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::mock_effect_handler::MockEffectHandler;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let cloud = crate::config::types::CloudConfig::disabled();
    let config = Config {
        developer_iters: 0,
        reviewer_reviews: 1,
        ..Config::default()
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

    let state = super::create_initial_state_with_config(&ctx);
    let mut handler = MockEffectHandler::new(state.clone());
    let loop_config = EventLoopConfig {
        max_iterations: 500,
    };

    let result = run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
        .expect("event loop should run");
    assert!(result.completed, "expected pipeline to complete");
    assert_eq!(handler.state.phase, PipelinePhase::Complete);

    let effects = handler.captured_effects();
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::PrepareReviewContext { .. })),
        "expected review to run when reviewer_reviews>0"
    );
    assert!(
        effects
            .iter()
            .any(|e| matches!(e, Effect::CreateCommit { .. })),
        "expected commit to occur after review"
    );
}

#[test]
fn test_event_trace_buffer_keeps_last_n_entries() {
    fn entry(iteration: usize) -> EventTraceEntry {
        EventTraceEntry {
            iteration,
            effect: format!("Effect{iteration}"),
            event: format!("Event{iteration}"),
            phase: "Planning".to_string(),
            xsd_retry_pending: false,
            xsd_retry_count: 0,
            invalid_output_attempts: 0,
            agent_index: 0,
            model_index: 0,
            retry_cycle: 0,
        }
    }

    let _cloud = crate::config::types::CloudConfig::disabled();
    let mut buf = EventTraceBuffer::new(3);
    for i in 0..5 {
        buf.push(entry(i));
    }

    let iterations: Vec<usize> = buf.entries().iter().map(|e| e.iteration).collect();
    assert_eq!(iterations, vec![2, 3, 4]);
}
