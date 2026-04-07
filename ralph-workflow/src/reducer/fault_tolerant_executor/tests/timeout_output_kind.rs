//! Tests for timeout output kind classification (Bug 2).
//!
//! Verifies that when an actual timeout occurs, the three outcome categories
//! (NoResult, PartialResult) are correctly distinguished, and that a valid result
//! file is promoted to success before timeout classification.

use super::*;

/// SIGTERM (exit 143) with no completion file → TimedOut with NoResult.
///
/// No result file means the agent likely crashed, hit an auth/API failure,
/// or was never able to start work.
#[test]
fn test_sigterm_without_result_file_returns_timed_out_no_result() {
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

    // No completion file
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

    match result.event {
        PipelineEvent::Agent(AgentEvent::TimedOut { output_kind, .. }) => {
            assert_eq!(
                output_kind,
                crate::reducer::event::TimeoutOutputKind::NoResult,
                "no completion file must produce NoResult timeout kind"
            );
        }
        other => panic!("Expected AgentEvent::TimedOut with NoResult, got: {other:?}"),
    }
}

/// SIGTERM (exit 143) with a partial/invalid completion file → TimedOut with PartialResult.
///
/// A result file exists but is invalid XML — agent started work but was interrupted
/// before writing a complete, parseable result.
#[test]
fn test_sigterm_with_partial_result_file_returns_timed_out_partial_result() {
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

    // Completion file exists but does not start with '<' (non-XML content written before
    // the XML root element — agent was interrupted mid-write before writing the tag).
    // has_valid_xml_output returns false (no leading '<'), but workspace.exists returns
    // true → determine_timeout_output_kind returns PartialResult.
    let workspace = MemoryWorkspace::new_test().with_file(
        ".agent/tmp/development_result.xml",
        "agent wrote some output before the XML root element was written",
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

    match result.event {
        PipelineEvent::Agent(AgentEvent::TimedOut { output_kind, .. }) => {
            assert_eq!(
                output_kind,
                crate::reducer::event::TimeoutOutputKind::PartialResult,
                "partial completion file must produce PartialResult timeout kind"
            );
        }
        other => panic!("Expected AgentEvent::TimedOut with PartialResult, got: {other:?}"),
    }
}
