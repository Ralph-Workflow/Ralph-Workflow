use anyhow::Result;

use super::error_handling::extract_error_event;
use super::trace::{build_trace_entry, dump_event_loop_trace, EventTraceBuffer};
use super::{
    create_initial_state_with_config, run_event_loop_with_handler, EventLoopConfig,
    MAX_EVENT_LOOP_ITERATIONS,
};
use crate::phases::PhaseContext;
use crate::reducer::PipelineState;

#[test]
fn test_dump_event_loop_trace_creates_parent_dir_before_write() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::{MemoryWorkspace, Workspace};
    use std::io;
    use std::path::{Path, PathBuf};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::Arc;

    #[derive(Debug, Clone)]
    struct StrictTmpWorkspace {
        inner: MemoryWorkspace,
        tmp_created: Arc<AtomicBool>,
    }

    impl StrictTmpWorkspace {
        fn new(inner: MemoryWorkspace) -> Self {
            Self {
                inner,
                tmp_created: Arc::new(AtomicBool::new(false)),
            }
        }
    }

    impl Workspace for StrictTmpWorkspace {
        fn root(&self) -> &Path {
            self.inner.root()
        }

        fn read(&self, relative: &Path) -> io::Result<String> {
            self.inner.read(relative)
        }

        fn read_bytes(&self, relative: &Path) -> io::Result<Vec<u8>> {
            self.inner.read_bytes(relative)
        }

        fn write(&self, relative: &Path, content: &str) -> io::Result<()> {
            // Check if this is an event_loop_trace.jsonl file in any run log directory
            if relative
                .to_string_lossy()
                .contains("event_loop_trace.jsonl")
                && !self.tmp_created.load(Ordering::Acquire)
            {
                return Err(io::Error::new(
                    io::ErrorKind::NotFound,
                    "parent dir missing (strict workspace)",
                ));
            }
            self.inner.write(relative, content)
        }

        fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.write_bytes(relative, content)
        }

        fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
            self.inner.append_bytes(relative, content)
        }

        fn exists(&self, relative: &Path) -> bool {
            self.inner.exists(relative)
        }

        fn is_file(&self, relative: &Path) -> bool {
            self.inner.is_file(relative)
        }

        fn is_dir(&self, relative: &Path) -> bool {
            self.inner.is_dir(relative)
        }

        fn remove(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove(relative)
        }

        fn remove_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_if_exists(relative)
        }

        fn remove_dir_all(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all(relative)
        }

        fn remove_dir_all_if_exists(&self, relative: &Path) -> io::Result<()> {
            self.inner.remove_dir_all_if_exists(relative)
        }

        fn create_dir_all(&self, relative: &Path) -> io::Result<()> {
            // Allow .agent/tmp directory creation (old behavior) and per-run log directories (new behavior)
            if relative == Path::new(".agent/tmp")
                || relative.to_string_lossy().starts_with(".agent/logs-")
            {
                self.tmp_created.store(true, Ordering::Release);
            }
            self.inner.create_dir_all(relative)
        }

        fn read_dir(&self, relative: &Path) -> io::Result<Vec<crate::workspace::DirEntry>> {
            self.inner.read_dir(relative)
        }

        fn rename(&self, from: &Path, to: &Path) -> io::Result<()> {
            self.inner.rename(from, to)
        }

        fn write_atomic(&self, relative: &Path, content: &str) -> io::Result<()> {
            self.inner.write_atomic(relative, content)
        }

        fn set_readonly(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_readonly(relative)
        }

        fn set_writable(&self, relative: &Path) -> io::Result<()> {
            self.inner.set_writable(relative)
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
    let strict_workspace = StrictTmpWorkspace::new(MemoryWorkspace::new(repo_root.clone()));
    let run_log_context = crate::logging::RunLogContext::new(&strict_workspace).unwrap();

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
        workspace: &strict_workspace,
        workspace_arc: Arc::new(strict_workspace.clone()),
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
    };

    let mut trace = EventTraceBuffer::new(1);
    let state = PipelineState::initial(1, 0);
    trace.push(build_trace_entry(0, &state, "Effect::None", "Event::None"));

    let dumped = dump_event_loop_trace(&ctx, &trace, &state, "test");
    assert!(
        dumped,
        "expected trace dump to succeed even when .agent/tmp is missing"
    );
    assert!(
        strict_workspace
            .inner
            .exists(&ctx.run_log_context.event_loop_trace()),
        "expected trace file to be created"
    );
}

#[test]
fn test_extract_error_event_searches_anyhow_error_chain() {
    use crate::reducer::event::ErrorEvent;
    use std::error::Error;
    use std::fmt;

    #[derive(Debug)]
    struct WrapperError {
        source: ErrorEvent,
    }

    impl fmt::Display for WrapperError {
        fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
            write!(f, "wrapper")
        }
    }

    impl Error for WrapperError {
        fn source(&self) -> Option<&(dyn Error + 'static)> {
            Some(&self.source)
        }
    }

    let wrapped: anyhow::Error = anyhow::Error::new(WrapperError {
        source: ErrorEvent::FixPromptMissing,
    });

    let extracted =
        extract_error_event(&wrapped).expect("expected ErrorEvent to be found in error chain");
    assert!(matches!(extracted, ErrorEvent::FixPromptMissing));
}

#[test]
fn test_event_loop_dumps_trace_on_unrecoverable_handler_error() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::phases::PhaseContext;
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
    use crate::reducer::state::PipelineState;
    use crate::workspace::{MemoryWorkspace, Workspace};
    use anyhow::Result;
    use std::path::{Path, PathBuf};
    use std::sync::Arc;

    #[derive(Debug)]
    struct UnrecoverableErrorHandler {
        state: PipelineState,
    }

    impl EffectHandler<'_> for UnrecoverableErrorHandler {
        fn execute(
            &mut self,
            _effect: Effect,
            _ctx: &mut PhaseContext<'_>,
        ) -> Result<EffectResult> {
            Err(anyhow::anyhow!("boom"))
        }
    }

    impl super::StatefulHandler for UnrecoverableErrorHandler {
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

    let state = PipelineState::initial(1, 0);
    let mut handler = UnrecoverableErrorHandler {
        state: state.clone(),
    };
    let loop_config = super::EventLoopConfig { max_iterations: 10 };

    let result =
        super::run_event_loop_with_handler(&mut ctx, Some(state), loop_config, &mut handler)
            .expect("event loop should return an EventLoopResult even on unrecoverable errors");
    assert!(
        !result.completed,
        "expected unrecoverable handler error to be reported as incomplete"
    );

    assert!(
        workspace.exists(&ctx.run_log_context.event_loop_trace()),
        "expected trace file to be dumped on unrecoverable handler error"
    );
    assert!(
        workspace.exists(Path::new(".agent/tmp/completion_marker")),
        "expected completion marker to be written on unrecoverable handler error"
    );
}

#[test]
fn test_event_loop_config_creation() {
    let _cloud = crate::config::types::CloudConfig::disabled();
    let config = EventLoopConfig {
        max_iterations: 1000,
    };
    assert_eq!(config.max_iterations, 1000);
}

#[test]
fn test_max_event_loop_iterations_is_one_million() {
    assert_eq!(MAX_EVENT_LOOP_ITERATIONS, 1_000_000);
}

#[test]
fn test_create_initial_state_with_config_counts_total_attempts() {
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

    // Semantics: max_dev_continuations counts *continuations beyond initial*.
    // Total attempts should be 1 + max_dev_continuations.
    let config = Config {
        max_dev_continuations: Some(2),
        max_xsd_retries: Some(10),
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
    };

    let state = create_initial_state_with_config(&ctx);

    assert_eq!(
        state.continuation.max_continue_count, 3,
        "max_continue_count should be total attempts (1 + max_dev_continuations)"
    );
}

#[test]
fn test_create_initial_state_with_config_allows_explicit_low_continuation_limits() {
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
    let cloud_config = crate::config::types::CloudConfig::disabled();

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let template_context = TemplateContext::default();
    let registry = AgentRegistry::new().unwrap();
    let executor = Arc::new(MockProcessExecutor::new());
    let repo_root = PathBuf::from("/test/repo");
    let workspace = MemoryWorkspace::new(repo_root.clone());
    let run_log_context = crate::logging::RunLogContext::new(&workspace).unwrap();

    for (max_dev_continuations, expected_max_continue_count) in [(0, 1), (1, 2)] {
        let config = Config {
            max_dev_continuations: Some(max_dev_continuations),
            max_xsd_retries: Some(10),
            ..Config::default()
        };

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
            cloud: &cloud_config,
        };

        let state = create_initial_state_with_config(&ctx);
        assert_eq!(
            state.continuation.max_continue_count, expected_max_continue_count,
            "explicit max_dev_continuations={max_dev_continuations} should map to max_continue_count={expected_max_continue_count}"
        );
    }
}

#[test]
fn test_create_initial_state_with_config_injects_cloud_state() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::types::{CloudConfig, GitAuthMethod, GitRemoteConfig};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    let config = Config {
        developer_iters: 1,
        reviewer_reviews: 0,
        ..Config::default()
    };

    let cloud = CloudConfig {
        enabled: true,
        api_url: Some("https://api.example.com/v1".to_string()),
        api_token: Some("secret".to_string()),
        run_id: Some("run_123".to_string()),
        heartbeat_interval_secs: 30,
        graceful_degradation: true,
        git_remote: GitRemoteConfig {
            auth_method: GitAuthMethod::SshKey { key_path: None },
            push_branch: Some("main".to_string()),
            create_pr: false,
            pr_title_template: None,
            pr_body_template: None,
            pr_base_branch: None,
            force_push: false,
            remote_name: "origin".to_string(),
        },
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
    };

    let state = create_initial_state_with_config(&ctx);

    assert!(
        state.cloud.enabled,
        "initial PipelineState must carry cloud enabled flag so orchestrator can emit cloud effects"
    );
}

/// Regression test: event loop must apply `EffectResult.additional_events`.
///
/// Without this, `AgentEvent::SessionEstablished` is never reduced and same-session
/// XSD retry cannot work.
#[test]
fn test_event_loop_applies_additional_events_in_order() {
    use crate::agents::AgentRegistry;
    use crate::checkpoint::{ExecutionHistory, RunContext};
    use crate::config::Config;
    use crate::executor::MockProcessExecutor;
    use crate::logger::{Colors, Logger};
    use crate::pipeline::Timer;
    use crate::prompts::template_context::TemplateContext;
    use crate::reducer::effect::{Effect, EffectHandler, EffectResult};
    use crate::reducer::event::PipelinePhase;
    use crate::reducer::state::PromptPermissionsState;
    use crate::reducer::PipelineEvent;
    use crate::workspace::MemoryWorkspace;
    use std::path::PathBuf;
    use std::sync::Arc;

    #[derive(Debug)]
    struct TestHandler {
        state: PipelineState,
    }

    impl TestHandler {
        fn new(state: PipelineState) -> Self {
            Self { state }
        }
    }

    impl EffectHandler<'_> for TestHandler {
        fn execute(
            &mut self,
            _effect: Effect,
            _ctx: &mut PhaseContext<'_>,
        ) -> Result<EffectResult> {
            Ok(
                EffectResult::event(PipelineEvent::prompt_permissions_restored())
                    .with_additional_event(PipelineEvent::agent_session_established(
                        crate::agents::AgentRole::Developer,
                        "test-agent".to_string(),
                        "session-123".to_string(),
                    )),
            )
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

    let state = PipelineState {
        phase: PipelinePhase::Finalizing,
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
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
        result.completed,
        "pipeline should complete (PromptPermissionsRestored)"
    );
    assert_eq!(
        handler.state.agent_chain.last_session_id.as_deref(),
        Some("session-123"),
        "additional SessionEstablished event should be reduced and stored"
    );
}
