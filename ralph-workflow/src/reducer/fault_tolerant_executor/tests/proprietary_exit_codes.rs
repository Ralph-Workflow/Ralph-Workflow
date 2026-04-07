//! Tests for proprietary agent exit codes with valid result files (Bug 1).
//!
//! Verifies that non-standard, non-timeout exit codes (e.g. reason:91 from OpenCode)
//! are treated as success when a valid result file exists, and as failure (not timeout)
//! when no valid result file is present.

use super::*;

/// Exit code 91 is OpenCode's proprietary stop reason ("stop, reason:91").
/// When a valid result file exists, the agent completed successfully — this
/// must not be classified as a timeout.
#[test]
fn test_exit_code_91_with_valid_result_file_returns_succeeded() {
    use crate::interrupt::{
        interrupt_test_lock, reset_user_interrupted_occurred, take_user_interrupt_request,
    };
    let _lock = interrupt_test_lock();
    let _ = take_user_interrupt_request();
    reset_user_interrupted_occurred();

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();

    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_result.xml",
        "<ralph-development-result><ralph-status>completed</ralph-status></ralph-development-result>",
    );

    let executor = Arc::new(
        crate::executor::MockProcessExecutor::new().with_agent_result(
            "opencode",
            Ok(crate::executor::AgentCommandResult::failure(91, "")),
        ),
    );
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor;

    let mut runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor_arc.as_ref(),
        executor_arc: Arc::clone(&executor_arc),
        workspace: &workspace,
        workspace_arc: Arc::new(workspace.clone()),
    };

    let env_vars: HashMap<String, String> = HashMap::new();
    let exec_config = AgentExecutionConfig {
        role: AgentRole::Developer,
        agent_name: "opencode",
        cmd_str: "opencode -p",
        parser_type: JsonParserType::OpenCode,
        env_vars: &env_vars,
        prompt: "implement the feature",
        display_name: "opencode",
        log_prefix: ".agent/logs/test",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/test.log",
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let result = execute_agent_fault_tolerantly(exec_config, &mut runtime)
        .expect("executor should never return Err");

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        ),
        "exit code 91 with valid result file must return InvocationSucceeded, got: {:?}",
        result.event
    );
}

/// Exit code 91 without a valid result file should return InvocationFailed,
/// NOT TimedOut — proprietary exit codes must never be misclassified as timeouts.
#[test]
fn test_exit_code_91_without_result_file_returns_failed_not_timed_out() {
    use crate::interrupt::{
        interrupt_test_lock, reset_user_interrupted_occurred, take_user_interrupt_request,
    };
    let _lock = interrupt_test_lock();
    let _ = take_user_interrupt_request();
    reset_user_interrupted_occurred();

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();

    // No result file present
    let workspace = MemoryWorkspace::new_test();

    let executor = Arc::new(
        crate::executor::MockProcessExecutor::new().with_agent_result(
            "opencode",
            Ok(crate::executor::AgentCommandResult::failure(91, "")),
        ),
    );
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor;

    let mut runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor_arc.as_ref(),
        executor_arc: Arc::clone(&executor_arc),
        workspace: &workspace,
        workspace_arc: Arc::new(workspace.clone()),
    };

    let env_vars: HashMap<String, String> = HashMap::new();
    let exec_config = AgentExecutionConfig {
        role: AgentRole::Developer,
        agent_name: "opencode",
        cmd_str: "opencode -p",
        parser_type: JsonParserType::OpenCode,
        env_vars: &env_vars,
        prompt: "implement the feature",
        display_name: "opencode",
        log_prefix: ".agent/logs/test",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/test.log",
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let result = execute_agent_fault_tolerantly(exec_config, &mut runtime)
        .expect("executor should never return Err");

    // Must not be a timeout — exit code 91 is NOT a timeout signal
    assert!(
        !matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::TimedOut { .. })
        ),
        "exit code 91 without result file must NOT return TimedOut; got: {:?}",
        result.event
    );

    // Must be InvocationFailed
    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationFailed { .. })
        ),
        "exit code 91 without result file must return InvocationFailed; got: {:?}",
        result.event
    );
}

/// SIGTERM (exit 143) with a valid result file should be promoted to success.
///
/// The idle timeout mechanism sends SIGTERM to clean up an already-finished
/// process. A valid result file proves the work was done before the signal arrived.
#[test]
fn test_exit_code_143_sigterm_with_valid_result_file_returns_succeeded() {
    use crate::interrupt::{
        interrupt_test_lock, reset_user_interrupted_occurred, take_user_interrupt_request,
    };
    let _lock = interrupt_test_lock();
    let _ = take_user_interrupt_request();
    reset_user_interrupted_occurred();

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();

    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_result.xml",
        "<ralph-development-result><ralph-status>completed</ralph-status></ralph-development-result>",
    );

    let executor = Arc::new(
        crate::executor::MockProcessExecutor::new().with_agent_result(
            "claude",
            Ok(crate::executor::AgentCommandResult::failure(143, "")),
        ),
    );
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor;

    let mut runtime = PipelineRuntime {
        timer: &mut timer,
        logger: &logger,
        colors: &colors,
        config: &config,
        executor: executor_arc.as_ref(),
        executor_arc: Arc::clone(&executor_arc),
        workspace: &workspace,
        workspace_arc: Arc::new(workspace.clone()),
    };

    let env_vars: HashMap<String, String> = HashMap::new();
    let exec_config = AgentExecutionConfig {
        role: AgentRole::Developer,
        agent_name: "claude",
        cmd_str: "claude -p",
        parser_type: JsonParserType::Claude,
        env_vars: &env_vars,
        prompt: "implement the feature",
        display_name: "claude",
        log_prefix: ".agent/logs/test",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/test.log",
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let result = execute_agent_fault_tolerantly(exec_config, &mut runtime)
        .expect("executor should never return Err");

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        ),
        "SIGTERM (143) with valid result file must return InvocationSucceeded, got: {:?}",
        result.event
    );
}
