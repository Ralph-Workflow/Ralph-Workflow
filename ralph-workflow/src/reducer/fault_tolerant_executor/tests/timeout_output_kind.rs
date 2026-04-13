//! Tests for timeout output kind classification (Bug 1 & Bug 2).
//!
//! ## Classification contract
//!
//! Timeout classification is driven by *explicit timeout evidence* only.
//! The `timeout_context` field on `CommandResult` is the single source of truth.
//! It is populated only when the idle-timeout monitor returns `MonitorResult::TimedOut`.
//!
//! Exit code 143 (SIGTERM) is classified by `classify_agent_error` as
//! `AgentErrorKind::Timeout`, but that classification alone does NOT emit
//! `AgentEvent::TimedOut`.  SIGTERM without `timeout_context` becomes
//! `AgentEvent::InvocationFailed` — SIGTERM may come from user interrupt or cleanup,
//! not only from the idle-timeout monitor.
//!
//! ## Required classification matrix
//!
//! | `timeout_context` | Valid result file | Expected event           |
//! |-------------------|------------------|--------------------------|
//! | None              | Yes              | InvocationSucceeded      |
//! | None              | No               | InvocationFailed         |
//! | Some              | Yes              | InvocationSucceeded      |
//! | Some              | No (absent)      | TimedOut(NoResult)       |
//! | Some              | No (invalid XML) | TimedOut(PartialResult)  |
//!
//! The `None` rows are exercised below through `execute_agent_fault_tolerantly` with
//! `MockProcessExecutor` (mock exits immediately → monitor never fires → timeout_context=None).
//!
//! The `Some` rows are exercised below through `classify_nonzero_command_result` with a
//! synthesized `CommandResult` that carries `timeout_context=Some`. This directly tests the
//! classification branch without requiring the idle-timeout monitor to fire (which would need
//! a real/hanging process and a 300 s timeout). The monitor-fires path is covered end-to-end
//! in `pipeline/prompt/tests/io_spawn_idle_timeout.rs`.

use super::*;

/// SIGTERM (exit 143) **without** explicit timeout context and no completion file
/// must return `InvocationFailed`, not `TimedOut`.
///
/// The monitor did NOT fire here — the mock executor exits immediately.
/// Only explicit `timeout_context` (set by the idle-timeout monitor) justifies
/// a `TimedOut` event.
#[test]
fn test_sigterm_without_timeout_context_and_no_result_returns_invocation_failed() {
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

    // No completion file; mock exits immediately with 143 (no monitor timeout).
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

    // Must NOT be a timeout event — SIGTERM without timeout_context is not a timeout.
    assert!(
        !matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::TimedOut { .. })
        ),
        "SIGTERM without timeout_context must NOT return TimedOut; got: {:?}",
        result.event
    );

    // Must be InvocationFailed.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationFailed { .. })
        ),
        "SIGTERM without timeout_context and no result must return InvocationFailed; got: {:?}",
        result.event
    );
}

/// SIGTERM (exit 143) **without** explicit timeout context but with a partial
/// (invalid XML) completion file must return `InvocationFailed`, not `TimedOut`.
///
/// The partial file does not constitute valid output, and without `timeout_context`
/// there is no basis for a `TimedOut` event.
#[test]
fn test_sigterm_without_timeout_context_with_partial_result_returns_invocation_failed() {
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

    // Completion file exists but is empty (agent was interrupted before writing).
    let workspace = MemoryWorkspace::new_test().with_file(".agent/tmp/development_result.xml", "");

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

    // Must NOT be a timeout event.
    assert!(
        !matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::TimedOut { .. })
        ),
        "SIGTERM without timeout_context must NOT return TimedOut even with partial file; got: {:?}",
        result.event
    );

    // Must be InvocationFailed.
    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationFailed { .. })
        ),
        "SIGTERM without timeout_context and partial result must return InvocationFailed; got: {:?}",
        result.event
    );
}

// ── executor-level tests for timeout_context=Some rows ───────────────────────
//
// These tests call `classify_nonzero_command_result` directly with a synthesized
// `CommandResult` that has `timeout_context=Some`, testing the full executor
// classification matrix rows where explicit timeout evidence is present.
//
// Building `CommandResult` with `timeout_context=Some` bypasses the idle-timeout
// monitor and tests only the classification logic inside the executor. This is
// sufficient because the monitor→CommandResult propagation is covered in the
// `pipeline::prompt::tests::io_spawn_idle_timeout` suite.

/// `timeout_context=Some` + valid result file → `InvocationSucceeded`
///
/// Even when the wall-clock timeout fired and killed the process, a valid result
/// file means the agent completed its work before the kill arrived. The result
/// must be promoted to success.
#[test]
fn test_explicit_timeout_with_valid_result_file_returns_invocation_succeeded() {
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();

    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_result.xml",
        "<ralph-development-result><ralph-status>completed</ralph-status></ralph-development-result>",
    );

    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor;

    let runtime = PipelineRuntime {
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
        parser_type: crate::agents::JsonParserType::Claude,
        env_vars: &env_vars,
        prompt: "implement the feature",
        display_name: "claude",
        log_prefix: ".agent/logs/test",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/test.log",
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let agent_name = crate::common::domain_types::AgentName::from("claude".to_string());
    let result_with_timeout = crate::pipeline::CommandResult {
        exit_code: 143,
        stderr: String::new(),
        session_id: None,
        child_status_at_timeout: None,
        timeout_context: Some(crate::pipeline::types::TimeoutContext {
            escalated: false,
            child_status_at_timeout: None,
        }),
    };

    let result =
        classify_nonzero_command_result(result_with_timeout, &exec_config, &agent_name, &runtime);

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        ),
        "timeout_context=Some with valid result file must return InvocationSucceeded; got: {:?}",
        result.event
    );
    // The runtime is borrowed mutably only for the timer, so consume it to avoid unused warning.
    let _ = runtime;
}

/// `timeout_context=Some` + absent result file → `TimedOut(NoResult)`
///
/// An explicit timeout with no output means the agent produced nothing before
/// being killed. Must emit `TimedOut` with `NoResult` output kind.
#[test]
fn test_explicit_timeout_with_no_result_file_returns_timed_out_no_result() {
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();

    // No completion file present.
    let workspace = MemoryWorkspace::new_test();

    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor;

    let runtime = PipelineRuntime {
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
        parser_type: crate::agents::JsonParserType::Claude,
        env_vars: &env_vars,
        prompt: "implement the feature",
        display_name: "claude",
        log_prefix: ".agent/logs/test",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/test.log",
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let agent_name = crate::common::domain_types::AgentName::from("claude".to_string());
    let result_with_timeout = crate::pipeline::CommandResult {
        exit_code: 143,
        stderr: String::new(),
        session_id: None,
        child_status_at_timeout: None,
        timeout_context: Some(crate::pipeline::types::TimeoutContext {
            escalated: false,
            child_status_at_timeout: None,
        }),
    };

    let result =
        classify_nonzero_command_result(result_with_timeout, &exec_config, &agent_name, &runtime);

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::TimedOut {
                output_kind: crate::reducer::event::TimeoutOutputKind::NoResult,
                ..
            })
        ),
        "timeout_context=Some with absent file must return TimedOut(NoResult); got: {:?}",
        result.event
    );
    let _ = runtime;
}

/// `timeout_context=Some` + present-but-empty result file → `TimedOut(PartialResult)`
///
/// A file exists but is empty — the agent started writing but didn't finish.
/// Must emit `TimedOut` with `PartialResult` output kind.
#[test]
fn test_explicit_timeout_with_invalid_result_file_returns_timed_out_partial_result() {
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();

    // Completion file exists but is empty (agent was interrupted before writing content).
    let workspace = MemoryWorkspace::new_test().with_file(".agent/tmp/development_result.xml", "");

    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor;

    let runtime = PipelineRuntime {
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
        parser_type: crate::agents::JsonParserType::Claude,
        env_vars: &env_vars,
        prompt: "implement the feature",
        display_name: "claude",
        log_prefix: ".agent/logs/test",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/test.log",
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let agent_name = crate::common::domain_types::AgentName::from("claude".to_string());
    let result_with_timeout = crate::pipeline::CommandResult {
        exit_code: 143,
        stderr: String::new(),
        session_id: None,
        child_status_at_timeout: None,
        timeout_context: Some(crate::pipeline::types::TimeoutContext {
            escalated: false,
            child_status_at_timeout: None,
        }),
    };

    let result =
        classify_nonzero_command_result(result_with_timeout, &exec_config, &agent_name, &runtime);

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::TimedOut {
                output_kind: crate::reducer::event::TimeoutOutputKind::PartialResult,
                ..
            })
        ),
        "timeout_context=Some with invalid file must return TimedOut(PartialResult); got: {:?}",
        result.event
    );
    let _ = runtime;
}

/// Analysis drain: explicit timeout + valid development_result.xml → `InvocationSucceeded`
///
/// Bug 2 regression: When the idle timeout monitor fires and kills the process
/// (explicit timeout_context), but the agent already produced a valid result file,
/// the result must be promoted to success. The timeout signal is irrelevant noise
/// when valid work was completed before the kill arrived.
#[test]
fn test_analysis_drain_explicit_timeout_with_valid_result_returns_invocation_succeeded() {
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();

    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_result.xml",
        "<ralph-development-result><ralph-status>completed</ralph-status></ralph-development-result>",
    );

    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor;

    let runtime = PipelineRuntime {
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
        agent_name: "claude",
        cmd_str: "claude -p",
        parser_type: crate::agents::JsonParserType::Claude,
        env_vars: &env_vars,
        prompt: "analyze the changes",
        display_name: "claude",
        log_prefix: ".agent/logs/analysis",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/analysis_1.log",
        // Analysis drain should use development_result.xml as completion path
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let agent_name = crate::common::domain_types::AgentName::from("claude".to_string());
    let result_with_timeout = crate::pipeline::CommandResult {
        exit_code: 143,
        stderr: String::new(),
        session_id: None,
        child_status_at_timeout: None,
        timeout_context: Some(crate::pipeline::types::TimeoutContext {
            escalated: false,
            child_status_at_timeout: None,
        }),
    };

    let result =
        classify_nonzero_command_result(result_with_timeout, &exec_config, &agent_name, &runtime);

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::InvocationSucceeded { .. })
        ),
        "timeout_context=Some with valid development_result.xml must return InvocationSucceeded; got: {:?}",
        result.event
    );
    let _ = runtime;
}

/// Analysis drain: explicit timeout + no result file → `TimedOut(NoResult)`
///
/// Bug 2 regression: When the idle timeout fires and no result file exists,
/// this is a genuine no-result timeout and must be classified as such.
#[test]
fn test_analysis_drain_explicit_timeout_with_no_result_returns_timed_out_no_result() {
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();

    let workspace = MemoryWorkspace::new_test();

    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor;

    let runtime = PipelineRuntime {
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
        agent_name: "claude",
        cmd_str: "claude -p",
        parser_type: crate::agents::JsonParserType::Claude,
        env_vars: &env_vars,
        prompt: "analyze the changes",
        display_name: "claude",
        log_prefix: ".agent/logs/analysis",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/analysis_1.log",
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let agent_name = crate::common::domain_types::AgentName::from("claude".to_string());
    let result_with_timeout = crate::pipeline::CommandResult {
        exit_code: 143,
        stderr: String::new(),
        session_id: None,
        child_status_at_timeout: None,
        timeout_context: Some(crate::pipeline::types::TimeoutContext {
            escalated: false,
            child_status_at_timeout: None,
        }),
    };

    let result =
        classify_nonzero_command_result(result_with_timeout, &exec_config, &agent_name, &runtime);

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::TimedOut {
                output_kind: crate::reducer::event::TimeoutOutputKind::NoResult,
                ..
            })
        ),
        "timeout_context=Some with absent result file must return TimedOut(NoResult); got: {:?}",
        result.event
    );
    let _ = runtime;
}

/// Analysis drain: explicit timeout + empty result file → `TimedOut(PartialResult)`
///
/// Bug 2 regression: When the idle timeout fires and a file exists but is empty,
/// the agent started but didn't finish writing. Must emit PartialResult.
#[test]
fn test_analysis_drain_explicit_timeout_with_invalid_result_returns_timed_out_partial_result() {
    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();

    let workspace = MemoryWorkspace::new_test().with_file(".agent/tmp/development_result.xml", "");

    let executor = Arc::new(crate::executor::MockProcessExecutor::new());
    let executor_arc: Arc<dyn crate::executor::ProcessExecutor> = executor;

    let runtime = PipelineRuntime {
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
        agent_name: "claude",
        cmd_str: "claude -p",
        parser_type: crate::agents::JsonParserType::Claude,
        env_vars: &env_vars,
        prompt: "analyze the changes",
        display_name: "claude",
        log_prefix: ".agent/logs/analysis",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/analysis_1.log",
        completion_output_path: Some(Path::new(".agent/tmp/development_result.xml")),
    };

    let agent_name = crate::common::domain_types::AgentName::from("claude".to_string());
    let result_with_timeout = crate::pipeline::CommandResult {
        exit_code: 143,
        stderr: String::new(),
        session_id: None,
        child_status_at_timeout: None,
        timeout_context: Some(crate::pipeline::types::TimeoutContext {
            escalated: false,
            child_status_at_timeout: None,
        }),
    };

    let result =
        classify_nonzero_command_result(result_with_timeout, &exec_config, &agent_name, &runtime);

    assert!(
        matches!(
            result.event,
            PipelineEvent::Agent(AgentEvent::TimedOut {
                output_kind: crate::reducer::event::TimeoutOutputKind::PartialResult,
                ..
            })
        ),
        "timeout_context=Some with invalid file must return TimedOut(PartialResult); got: {:?}",
        result.event
    );
    let _ = runtime;
}

// ── determine_timeout_output_kind unit tests ──────────────────────────────────
//
// These tests exercise the helper that classifies timed-out output as NoResult
// or PartialResult. This helper is called ONLY when `timeout_context=Some`
// (i.e., after a definitive wall-clock timeout from the idle-timeout monitor)
// AND the result file does NOT contain valid XML (the valid-result early-return
// is checked upstream before this helper is invoked).

/// When `completion_output_path` is set and the file is absent,
/// `determine_timeout_output_kind` must return `NoResult`.
#[test]
fn test_determine_timeout_output_kind_no_completion_path_file_absent_returns_no_result() {
    let workspace = MemoryWorkspace::new_test();
    let result = determine_timeout_output_kind(
        ".agent/logs/test.log",
        Some(Path::new(".agent/tmp/development_result.xml")),
        &workspace,
    );
    assert_eq!(
        result,
        crate::reducer::event::TimeoutOutputKind::NoResult,
        "absent completion file must produce NoResult"
    );
}

/// When `completion_output_path` is set and the file exists (even with non-XML
/// content), `determine_timeout_output_kind` must return `PartialResult`.
/// The valid-XML check is performed upstream; by the time this function is reached,
/// `has_valid_xml_output` has already returned `false`, so "exists but invalid" → PartialResult.
#[test]
fn test_determine_timeout_output_kind_completion_path_file_exists_invalid_xml_returns_partial() {
    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_result.xml",
        "agent started writing but did not finish",
    );
    let result = determine_timeout_output_kind(
        ".agent/logs/test.log",
        Some(Path::new(".agent/tmp/development_result.xml")),
        &workspace,
    );
    assert_eq!(
        result,
        crate::reducer::event::TimeoutOutputKind::PartialResult,
        "present-but-invalid completion file must produce PartialResult"
    );
}

/// When no `completion_output_path` is set and the logfile has sufficient content,
/// `determine_timeout_output_kind` must return `PartialResult`.
#[test]
fn test_determine_timeout_output_kind_no_completion_path_logfile_has_content_returns_partial() {
    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/logs/test.log",
        "This is more than ten non-whitespace characters of agent output.",
    );
    let result = determine_timeout_output_kind(".agent/logs/test.log", None, &workspace);
    assert_eq!(
        result,
        crate::reducer::event::TimeoutOutputKind::PartialResult,
        "logfile with sufficient content and no completion path must produce PartialResult"
    );
}

/// When no `completion_output_path` is set and the logfile has no content,
/// `determine_timeout_output_kind` must return `NoResult`.
#[test]
fn test_determine_timeout_output_kind_no_completion_path_empty_logfile_returns_no_result() {
    let workspace = MemoryWorkspace::new_test().with_file(".agent/logs/test.log", "   ");
    let result = determine_timeout_output_kind(".agent/logs/test.log", None, &workspace);
    assert_eq!(
        result,
        crate::reducer::event::TimeoutOutputKind::NoResult,
        "empty/whitespace-only logfile with no completion path must produce NoResult"
    );
}

/// When no `completion_output_path` is set and the logfile cannot be read
/// (e.g., does not exist), `determine_timeout_output_kind` must return `NoResult`
/// as a fail-safe (prefer immediate agent switching over retrying a broken agent).
#[test]
fn test_determine_timeout_output_kind_no_completion_path_missing_logfile_returns_no_result() {
    let workspace = MemoryWorkspace::new_test(); // logfile not written
    let result = determine_timeout_output_kind(".agent/logs/test.log", None, &workspace);
    assert_eq!(
        result,
        crate::reducer::event::TimeoutOutputKind::NoResult,
        "missing logfile with no completion path must produce NoResult (fail-safe)"
    );
}
