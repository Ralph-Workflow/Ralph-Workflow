use super::common::TestFixture;
use crate::agents::AgentRole;
use crate::executor::MockProcessExecutor;
use crate::logger::{Colors, Logger};
use crate::phases::PhaseContext;
use crate::reducer::effect::{Effect, EffectHandler};
use crate::reducer::event::{AwaitingDevFixEvent, PipelineEvent, PipelinePhase};
use crate::reducer::handler::MainEffectHandler;
use crate::reducer::state::AgentChainState;
use crate::reducer::state::PipelineState;
use crate::workspace::{MemoryWorkspace, Workspace};
use std::io;
use std::path::Path;
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
        if relative == Path::new(".agent/tmp/completion_marker")
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
        if relative == Path::new(".agent/tmp") {
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

#[derive(Debug, Clone)]
struct FailingMarkerWorkspace {
    inner: MemoryWorkspace,
}

impl FailingMarkerWorkspace {
    fn new(inner: MemoryWorkspace) -> Self {
        Self { inner }
    }
}

impl Workspace for FailingMarkerWorkspace {
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
        if relative == Path::new(".agent/tmp/completion_marker") {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "simulated marker write failure",
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

#[derive(Debug)]
struct FailingAgentLogWorkspace {
    inner: MemoryWorkspace,
}

impl Workspace for FailingAgentLogWorkspace {
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
        self.inner.write(relative, content)
    }

    fn write_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        self.inner.write_bytes(relative, content)
    }

    fn append_bytes(&self, relative: &Path, content: &[u8]) -> io::Result<()> {
        if relative.to_string_lossy().starts_with(".agent/logs-") {
            return Err(io::Error::other("usage limit exceeded"));
        }
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

#[test]
fn emit_completion_marker_creates_tmp_dir_before_write() {
    let mut fixture = TestFixture::new();
    let strict_ws = StrictTmpWorkspace::new(fixture.workspace.clone());
    let mut ctx = fixture.ctx_with_workspace(&strict_ws);

    let state = PipelineState::initial(1, 0);
    let mut handler = MainEffectHandler::new(state);

    let result = handler.execute(
        Effect::EmitCompletionMarkerAndTerminate {
            is_failure: true,
            reason: Some("unit test".to_string()),
        },
        &mut ctx,
    );

    assert!(
        result.is_ok(),
        "EmitCompletionMarkerAndTerminate should succeed"
    );

    let marker_path = Path::new(".agent/tmp/completion_marker");
    assert!(
        strict_ws.exists(marker_path),
        "Completion marker should exist"
    );
}

#[test]
fn emit_completion_marker_with_write_failure_emits_event() {
    let mut fixture = TestFixture::new();
    let failing_ws = FailingMarkerWorkspace::new(fixture.workspace.clone());
    let mut ctx = fixture.ctx_with_workspace(&failing_ws);

    let state = PipelineState::initial(1, 0);
    let mut handler = MainEffectHandler::new(state);

    let result = handler.execute(
        Effect::EmitCompletionMarkerAndTerminate {
            is_failure: true,
            reason: Some("unit test".to_string()),
        },
        &mut ctx,
    );

    assert!(
        result.is_ok(),
        "EmitCompletionMarkerAndTerminate should emit event even if write fails"
    );

    let event = result.expect("Expected effect result").event;
    assert!(matches!(
        event,
        PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::CompletionMarkerWriteFailed {
            is_failure: true,
            ..
        })
    ));
}

#[test]
fn trigger_dev_fix_flow_writes_marker_even_when_agent_invocation_fails() {
    let mut fixture = TestFixture::new();
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "missing-agent";

    let state = PipelineState::initial(1, 0);
    let mut handler = MainEffectHandler::new(state);

    let result = handler.execute(
        Effect::TriggerDevFixFlow {
            failed_phase: PipelinePhase::Development,
            failed_role: AgentRole::Developer,
            retry_cycle: 1,
        },
        &mut ctx,
    );

    assert!(
        result.is_ok(),
        "TriggerDevFixFlow should succeed even if dev-fix invocation fails"
    );

    let result = result.expect("Expected effect result");
    // NEW BEHAVIOR: CompletionMarkerEmitted should NOT be emitted immediately.
    // Instead, the pipeline should prepare for recovery after dev-fix completes.
    // The reducer will decide whether to retry, escalate, or terminate based on
    // the attempt count and recovery escalation level.
    assert!(
        !result.additional_events.iter().any(|event| matches!(
            event,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::CompletionMarkerEmitted {
                is_failure: true
            })
        )),
        "CompletionMarkerEmitted should NOT be emitted immediately on dev-fix invocation failure. \
         Recovery should be attempted first."
    );

    assert!(
        result.additional_events.iter().any(|event| matches!(
            event,
            PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixCompleted { .. })
        )),
        "DevFixCompleted should be emitted so the recovery loop can advance"
    );

    let marker_path = Path::new(".agent/tmp/completion_marker");
    assert!(
        !fixture.workspace.exists(marker_path),
        "Completion marker must not be written by TriggerDevFixFlow; it is written only on termination"
    );
}

#[test]
fn trigger_dev_fix_flow_invokes_configured_developer_agent_not_current_chain_agent() {
    let mut fixture = TestFixture::new();
    fixture.executor = Arc::new(
        MockProcessExecutor::new()
            .with_agent_result("claude", Ok(crate::executor::AgentCommandResult::success())),
    );
    let mut ctx = fixture.ctx();
    ctx.developer_agent = "claude";

    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::AwaitingDevFix;

    // Simulate failure outside Development: chain currently points at a different role/agent.
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["codex".to_string()],
        vec![vec![]],
        AgentRole::Commit,
    );

    let mut handler = MainEffectHandler::new(state);
    handler
        .execute(
            Effect::TriggerDevFixFlow {
                failed_phase: PipelinePhase::CommitMessage,
                failed_role: AgentRole::Commit,
                retry_cycle: 1,
            },
            &mut ctx,
        )
        .expect("TriggerDevFixFlow should succeed");

    let calls = fixture.executor.agent_calls();
    assert_eq!(calls.len(), 1);
    assert_eq!(calls[0].command, "claude");
}

#[test]
fn dev_fix_agent_unavailable_log_does_not_claim_termination() {
    // This test needs a custom Logger with `.with_workspace_log()`, so we build
    // the PhaseContext mostly inline but reuse TestFixture for shared scaffolding.
    let fixture = TestFixture::new();

    let failing_ws = Arc::new(FailingAgentLogWorkspace {
        inner: fixture.workspace.clone(),
    });
    let run_log_context = crate::logging::RunLogContext::new(failing_ws.as_ref()).unwrap();

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors).with_workspace_log(
        Arc::clone(&failing_ws) as Arc<dyn Workspace>,
        ".agent/tmp/test_logger.log",
    );

    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> =
        Arc::clone(&fixture.executor) as Arc<dyn crate::executor::ProcessExecutor>;

    let cloud = crate::config::types::CloudConfig::disabled();
    let workspace_arc = Arc::clone(&failing_ws) as Arc<dyn Workspace>;
    let mut timer = crate::pipeline::Timer::new();
    let git_env = crate::runtime::environment::mock::MockGitEnvironment::new();
    let mut ctx = PhaseContext {
        config: &fixture.config,
        registry: &fixture.registry,
        logger: &logger,
        colors: &colors,
        timer: &mut timer,
        developer_agent: "claude",
        reviewer_agent: "test-reviewer",
        review_guidelines: None,
        template_context: &fixture.template_context,
        run_context: crate::checkpoint::RunContext::new(),
        execution_history: crate::checkpoint::ExecutionHistory::new(),
        executor: executor_arc.as_ref(),
        executor_arc: Arc::clone(&executor_arc),
        repo_root: fixture.repo_root.as_path(),
        workspace: failing_ws.as_ref(),
        workspace_arc,
        run_log_context: &run_log_context,
        cloud_reporter: None,
        cloud: &cloud,
        env: &git_env,
    };

    let mut state = PipelineState::initial(1, 0);
    state.phase = PipelinePhase::AwaitingDevFix;
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["claude".to_string()],
        vec![vec![]],
        AgentRole::Developer,
    );
    let mut handler = MainEffectHandler::new(state);

    let result = handler
        .execute(
            Effect::TriggerDevFixFlow {
                failed_phase: PipelinePhase::Development,
                failed_role: AgentRole::Developer,
                retry_cycle: 1,
            },
            &mut ctx,
        )
        .expect("TriggerDevFixFlow should handle agent unavailability");

    assert!(result.additional_events.iter().any(|e| matches!(
        e,
        PipelineEvent::AwaitingDevFix(AwaitingDevFixEvent::DevFixAgentUnavailable { .. })
    )));

    let log_contents = failing_ws
        .read(Path::new(".agent/tmp/test_logger.log"))
        .expect("expected logger to write to workspace log");
    assert!(
        log_contents.contains("Continuing unattended recovery loop without dev-fix agent"),
        "expected updated log message, got:\n{log_contents}"
    );
    assert!(
        !log_contents.contains("WARNING: PIPELINE FAILURE DETECTED"),
        "log must not emit the legacy WARNING banner (it can trip verify output classifiers), got:\n{log_contents}"
    );
    assert!(
        !log_contents.contains("terminate with failure marker"),
        "log must not claim termination, got:\n{log_contents}"
    );
}
