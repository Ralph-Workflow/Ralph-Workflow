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

/// SIGTERM (exit 143) **without** explicit timeout context and **no** result file must
/// return `InvocationFailed`, not `TimedOut`.
///
/// `classify_agent_error(143, ...)` maps SIGTERM to `AgentErrorKind::Timeout`, but
/// that classification alone does NOT warrant a `TimedOut` event.  Only explicit
/// `timeout_context` (set by the idle-timeout monitor) justifies `TimedOut`.
#[test]
fn test_exit_code_143_sigterm_without_timeout_context_and_no_result_returns_invocation_failed() {
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

    // No result file; mock exits immediately (monitor never fires → timeout_context is None).
    let workspace = MemoryWorkspace::new_test();

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
        !matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::TimedOut { .. })
        ),
        "SIGTERM without timeout_context must NOT return TimedOut; got: {:?}",
        result.event
    );

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationFailed { .. })
        ),
        "SIGTERM without timeout_context and no result must return InvocationFailed; got: {:?}",
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

/// Rate limit error in stderr + valid result file → `InvocationSucceeded`
///
/// The agent may have been rate-limited on a *subsequent* API call after it already
/// produced a valid result. The valid result file proves the work is done; the
/// rate-limit signal must not override it.
///
/// This verifies that the result-file check has higher precedence than rate-limit
/// detection (Bug 3 fix per plan step 3.4 precedence policy).
#[test]
fn test_rate_limit_error_with_valid_result_file_returns_succeeded_not_rate_limited() {
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

    // Valid result file present — agent completed work before hitting rate limit.
    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_result.xml",
        "<ralph-development-result><ralph-status>completed</ralph-status></ralph-development-result>",
    );

    // Agent exits with non-zero and rate-limit in stderr.
    let executor = Arc::new(
        crate::executor::MockProcessExecutor::new().with_agent_result(
            "claude",
            Ok(crate::executor::AgentCommandResult::failure(
                1,
                "Rate limit exceeded: too many requests",
            )),
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

    // Must be InvocationSucceeded — valid result overrides rate-limit signal.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        ),
        "rate-limit with valid result file must return InvocationSucceeded, not RateLimited; got: {:?}",
        result.event
    );

    // Must NOT be RateLimited.
    assert!(
        !matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::RateLimited { .. })
        ),
        "rate-limit with valid result file must NOT return RateLimited; got: {:?}",
        result.event
    );
}

/// Analysis drain with exit code 91 and valid development_result.xml → `InvocationSucceeded`
///
/// Bug 1 regression: Analysis drain incorrectly returns None for completion_path,
/// causing exit code 91 (a proprietary stop reason, NOT a timeout) to be
/// misclassified. With the correct completion_path wiring, a valid result file
/// must promote the result to success regardless of exit code 91.
#[test]
fn test_analysis_drain_exit_code_91_with_valid_result_returns_succeeded() {
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

    // Valid development_result.xml present — same file Analysis agent writes
    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_result.xml",
        "<ralph-development-result><ralph-status>completed</ralph-status></ralph-development-result>",
    );

    // Agent exits with exit code 91 (OpenCode proprietary "stop, reason:91")
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
        role: AgentRole::Analysis,
        agent_name: "opencode",
        cmd_str: "opencode -p",
        parser_type: JsonParserType::OpenCode,
        env_vars: &env_vars,
        prompt: "analyze the changes",
        display_name: "opencode",
        log_prefix: ".agent/logs/analysis",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/analysis_1.log",
        // This is the completion path Analysis drain SHOULD use
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let result = execute_agent_fault_tolerantly(exec_config, &mut runtime)
        .expect("executor should never return Err");

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        ),
        "exit code 91 with valid development_result.xml must return InvocationSucceeded; got: {:?}",
        result.event
    );
}

/// Analysis drain with exit code 91 and no result file → `InvocationFailed`
///
/// Bug 1 regression: When no valid result file exists, exit code 91 must NOT be
/// treated as a timeout. It should be `InvocationFailed` with AgentErrorKind::Error
/// (or the proprietary error kind for 91).
#[test]
fn test_analysis_drain_exit_code_91_without_result_returns_failed_not_timed_out() {
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
        role: AgentRole::Analysis,
        agent_name: "opencode",
        cmd_str: "opencode -p",
        parser_type: JsonParserType::OpenCode,
        env_vars: &env_vars,
        prompt: "analyze the changes",
        display_name: "opencode",
        log_prefix: ".agent/logs/analysis",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/analysis_1.log",
        // Analysis drain currently passes None, which bypasses result file check
        // After fix, this should be set to DEVELOPMENT_RESULT_XML
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let result = execute_agent_fault_tolerantly(exec_config, &mut runtime)
        .expect("executor should never return Err");

    // Must NOT be a timeout — exit code 91 is NOT a timeout signal
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

/// Auth failure error in stderr + valid result file → `InvocationSucceeded`
///
/// An authentication failure may appear in stderr even when the agent already
/// produced a valid result (e.g., the agent completed its work before the auth
/// token expired). The valid result file proves completion; auth failure must
/// not override it.
#[test]
fn test_auth_failure_with_valid_result_file_returns_succeeded_not_auth_failed() {
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

    // Valid result file present.
    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_result.xml",
        "<ralph-development-result><ralph-status>completed</ralph-status></ralph-development-result>",
    );

    // Agent exits with non-zero and auth failure in stderr.
    let executor = Arc::new(
        crate::executor::MockProcessExecutor::new().with_agent_result(
            "claude",
            Ok(crate::executor::AgentCommandResult::failure(
                1,
                "Invalid API key provided",
            )),
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

    // Must be InvocationSucceeded — valid result overrides auth failure signal.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        ),
        "auth failure with valid result file must return InvocationSucceeded, not AuthFailed; got: {:?}",
        result.event
    );

    // Must NOT be AuthFailed.
    assert!(
        !matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::AuthFailed { .. })
        ),
        "auth failure with valid result file must NOT return AuthFailed; got: {:?}",
        result.event
    );
}
