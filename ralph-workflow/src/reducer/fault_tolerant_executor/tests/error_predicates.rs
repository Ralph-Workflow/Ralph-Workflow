//! Error predicate and fallback tests
//!
//! Tests for error type predicates and fallback behavior:
//! - Usage limit triggers `rate_limited` event (not timeout)

use super::*;

#[test]
fn test_usage_limit_triggers_rate_limited_event_not_timeout() {
    // Integration test: Usage limit errors trigger immediate agent fallback
    //
    // This test verifies the fix for the bug where "usage limit has been reached"
    // errors from OpenCode/Claude API caused the pipeline to timeout instead of
    // immediately falling back to the next agent.
    //
    // **Bug Report Context:**
    // OpenCode emits "usage limit has been reached [retryin]" when any underlying
    // provider (OpenAI, Anthropic, etc.) hits quota limits. The "[retryin]" suffix
    // is misleading - the agent is actually unavailable due to quota exhaustion.
    //
    // **Expected Behavior:**
    // The error should be classified as AgentErrorKind::RateLimit, which triggers
    // immediate agent fallback via AgentEvent::RateLimited (not timeout).
    //
    // **Verification:**
    // - Mock executor returns "usage limit has been reached [retryin]" error
    // - Executor result is AgentEvent::RateLimited (not TimedOut)
    // - No session_id is returned (provider is unavailable)

    use crate::executor::AgentCommandResult;
    use crate::interrupt::{
        interrupt_test_lock, reset_user_interrupted_occurred, take_user_interrupt_request,
    };

    // This test must not be affected by other tests that toggle the process-global
    // interrupt flags.
    let _lock = interrupt_test_lock();
    let _ = take_user_interrupt_request();
    reset_user_interrupted_occurred();

    let colors = Colors { enabled: false };
    let logger = Logger::new(colors);
    let mut timer = Timer::new();
    let config = Config::default();
    let workspace = MemoryWorkspace::new_test();

    // Mock executor that simulates usage limit error
    let executor = Arc::new(
        crate::executor::MockProcessExecutor::new().with_agent_result(
            "opencode",
            Ok(AgentCommandResult::failure(
                1,
                "Error: The usage limit has been reached [retryin]",
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
        workspace_arc: std::sync::Arc::new(workspace.clone()),
    };

    let env_vars: HashMap<String, String> = HashMap::new();
    let exec_config = AgentExecutionConfig {
        role: AgentRole::Developer,
        agent_name: "opencode",
        cmd_str: "opencode -p",
        parser_type: JsonParserType::Claude,
        env_vars: &env_vars,
        prompt: "Test prompt",
        display_name: "opencode",
        log_prefix: ".agent/logs/test",
        model_index: 0,
        attempt: 0,
        logfile: ".agent/logs/test.log",
    };

    let result = execute_agent_fault_tolerantly(exec_config, &mut runtime)
        .expect("executor should never return Err");

    // Verify that RateLimited event is emitted (not TimedOut or InvocationFailed)
    match result.event {
        PipelineEvent::Agent(AgentEvent::RateLimited { role, agent, .. }) => {
            assert_eq!(role, AgentRole::Developer);
            assert_eq!(agent, AgentName::from("opencode"));
        }
        other => panic!(
            "Expected AgentEvent::RateLimited, got {other:?}. \
             This indicates usage limit errors are not triggering immediate agent fallback."
        ),
    }

    // Verify no session_id is returned (rate limit = provider unavailable)
    assert!(result.session_id.is_none());
}
