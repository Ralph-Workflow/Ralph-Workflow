//! Retry scenario tests.
//!
//! Tests for rate limit handling, same-agent retry with transient failures,
//! and retry budget management.

use crate::agents::AgentRole;
use crate::common::domain_types::AgentName;
use crate::reducer::create_test_state;
use crate::reducer::event::{AgentErrorKind, PipelineEvent, PipelinePhase, TimeoutOutputKind};
use crate::reducer::state::{
    AgentChainState, ContinuationState, PipelineState, RateLimitContinuationPrompt,
    SameAgentRetryReason,
};
use crate::reducer::state_reduction::reduce;

#[test]
fn test_rate_limit_fallback_switches_agent() {
    let state = create_test_state();
    let initial_agent_index = state.agent_chain.current_agent_index;

    let new_state = reduce(
        state,
        PipelineEvent::agent_rate_limited(
            AgentRole::Developer,
            AgentName::from("agent1"),
            Some("test prompt".to_string()),
        ),
    );

    // Should switch to next agent
    assert!(
        new_state.agent_chain.current_agent_index > initial_agent_index,
        "Rate limit should trigger agent fallback, not model fallback"
    );
    // Should preserve prompt
    assert_eq!(
        new_state.agent_chain.rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "test prompt".to_string(),
        })
    );
}

#[test]
fn test_rate_limit_fallback_with_no_prompt_context() {
    let state = create_test_state();
    let initial_agent_index = state.agent_chain.current_agent_index;

    let new_state = reduce(
        state,
        PipelineEvent::agent_rate_limited(AgentRole::Developer, AgentName::from("agent1"), None),
    );

    // Should still switch to next agent
    assert!(new_state.agent_chain.current_agent_index > initial_agent_index);
    // Prompt context should be None
    assert!(new_state
        .agent_chain
        .rate_limit_continuation_prompt
        .is_none());
}

#[test]
fn test_success_clears_rate_limit_continuation_prompt() {
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: AgentChainState {
            rate_limit_continuation_prompt: Some(RateLimitContinuationPrompt {
                drain: crate::agents::AgentDrain::Development,
                role: AgentRole::Developer,
                prompt: "old prompt".to_string(),
            }),
            ..base_state.agent_chain
        },
        ..base_state
    };

    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_succeeded(AgentRole::Developer, AgentName::from("agent1")),
    );

    assert!(
        new_state
            .agent_chain
            .rate_limit_continuation_prompt
            .is_none(),
        "Success should clear rate limit continuation prompt"
    );
}

#[test]
fn test_legacy_rate_limit_failure_clears_stale_rate_limit_continuation_prompt() {
    // Regression: legacy callers may emit InvocationFailed{ error_kind: RateLimit } without
    // prompt_context. In that case we must NOT carry forward any previously saved continuation
    // prompt, otherwise the next invocation may run with stale prompt context.
    let base_state = create_test_state();
    let state = PipelineState {
        agent_chain: AgentChainState {
            rate_limit_continuation_prompt: Some(RateLimitContinuationPrompt {
                drain: crate::agents::AgentDrain::Development,
                role: AgentRole::Developer,
                prompt: "stale prompt".to_string(),
            }),
            ..base_state.agent_chain
        },
        ..base_state
    };

    let new_state = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            AgentName::from("agent1"),
            429,
            AgentErrorKind::RateLimit,
            false,
        ),
    );

    assert!(
        new_state
            .agent_chain
            .rate_limit_continuation_prompt
            .is_none(),
        "Legacy RateLimit failures must clear any previously saved continuation prompt"
    );
}

#[test]
fn test_rate_limit_continuation_prompt_is_preserved_until_success_even_across_retries() {
    // Regression: after a 429 RateLimited event, the continuation prompt must remain available
    // for retries until an invocation actually succeeds. Clearing it on InvocationStarted (or
    // on retryable failures like Timeout/InternalError) breaks "continue same prompt after 429"
    // when the first post-rate-limit invocation fails.
    let base_state = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        continuation: ContinuationState::with_limits(3, 2),
        ..base_state
    };

    let after_rate_limit = reduce(
        state,
        PipelineEvent::agent_rate_limited(
            AgentRole::Developer,
            AgentName::from("agent1"),
            Some("saved prompt".to_string()),
        ),
    );
    assert_eq!(
        after_rate_limit.agent_chain.current_agent().unwrap(),
        "agent2"
    );
    assert_eq!(
        after_rate_limit.agent_chain.rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        })
    );

    let after_started = reduce(
        after_rate_limit,
        PipelineEvent::agent_invocation_started(
            AgentRole::Developer,
            AgentName::from("agent2"),
            None,
        ),
    );
    assert_eq!(
        after_started.agent_chain.rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        }),
        "InvocationStarted must not clear rate-limit continuation prompt"
    );

    let after_failure = reduce(
        after_started,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            AgentName::from("agent2"),
            139,
            AgentErrorKind::InternalError,
            false,
        ),
    );
    assert_eq!(after_failure.agent_chain.current_agent().unwrap(), "agent2");
    assert!(
        after_failure.continuation.same_agent_retry_pending,
        "InternalError should schedule a same-agent retry"
    );
    assert_eq!(
        after_failure.agent_chain.rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        }),
        "Same-agent retry must preserve rate-limit continuation prompt"
    );

    let after_retry_started = reduce(
        after_failure,
        PipelineEvent::agent_invocation_started(
            AgentRole::Developer,
            AgentName::from("agent2"),
            None,
        ),
    );
    assert_eq!(
        after_retry_started
            .agent_chain
            .rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        }),
        "Retry InvocationStarted must preserve rate-limit continuation prompt"
    );

    let after_success = reduce(
        after_retry_started,
        PipelineEvent::agent_invocation_succeeded(AgentRole::Developer, AgentName::from("agent2")),
    );
    assert!(
        after_success
            .agent_chain
            .rate_limit_continuation_prompt
            .is_none(),
        "InvocationSucceeded should clear the consumed rate-limit continuation prompt"
    );
}

#[test]
fn test_auth_fallback_clears_session_and_advances_agent() {
    let base_chain = AgentChainState::initial()
        .with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        )
        .with_session_id(Some("session-123".to_string()));
    let chain = AgentChainState {
        rate_limit_continuation_prompt: Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "some saved prompt".to_string(),
        }),
        ..base_chain
    };

    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: chain,
        ..PipelineState::initial(5, 2)
    };

    let new_state = reduce(
        state,
        PipelineEvent::agent_auth_failed(AgentRole::Developer, AgentName::from("agent1")),
    );

    // Should advance to next agent
    assert_eq!(new_state.agent_chain.current_agent().unwrap(), "agent2");

    // Session should be cleared
    assert!(new_state.agent_chain.last_session_id.is_none());

    // Auth fallback semantics: switch agents WITHOUT prompt context.
    // Any previously-saved rate-limit continuation prompt must be cleared so we
    // don't accidentally carry prompt context across an auth fallback.
    assert!(
        new_state
            .agent_chain
            .rate_limit_continuation_prompt
            .is_none(),
        "AuthFailed must clear any existing continuation prompt"
    );
}

#[test]
fn test_rate_limit_fallback_clears_session_id() {
    // RateLimited preserves prompt context, but MUST NOT preserve session IDs
    // across agents.
    let chain = AgentChainState::initial()
        .with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        )
        .with_session_id(Some("session-123".to_string()));

    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: chain,
        ..PipelineState::initial(5, 2)
    };

    let new_state = reduce(
        state,
        PipelineEvent::agent_rate_limited(
            AgentRole::Developer,
            AgentName::from("agent1"),
            Some("preserved prompt".to_string()),
        ),
    );

    assert!(
        new_state.agent_chain.last_session_id.is_none(),
        "RateLimited must clear session IDs when switching agents"
    );
    assert_eq!(
        new_state.agent_chain.rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "preserved prompt".to_string(),
        }),
        "RateLimited should preserve prompt context"
    );
}

#[test]
fn test_auth_fallback_does_not_set_continuation_prompt() {
    // Setup: state with NO existing continuation prompt
    let chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );

    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: chain,
        ..PipelineState::initial(5, 2)
    };

    // Auth fallback should NOT set a continuation prompt
    let new_state = reduce(
        state,
        PipelineEvent::agent_auth_failed(AgentRole::Developer, AgentName::from("agent1")),
    );

    // Key assertion: AuthFailed does NOT set prompt context (unlike RateLimited).
    assert!(
        new_state
            .agent_chain
            .rate_limit_continuation_prompt
            .is_none(),
        "AuthFailed should not set continuation prompt (only RateLimited does)"
    );
}

#[test]
fn test_rate_limit_vs_auth_fallback_prompt_semantics() {
    // This test documents the key semantic difference between rate limit and auth failures.
    let base_chain = AgentChainState::initial().with_agents(
        vec![
            "agent1".to_string(),
            "agent2".to_string(),
            "agent3".to_string(),
        ],
        vec![vec![], vec![], vec![]],
        AgentRole::Developer,
    );

    // Test 1: RateLimited preserves prompt
    let state1 = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: base_chain.clone(),
        ..PipelineState::initial(5, 2)
    };

    let after_rate_limit = reduce(
        state1,
        PipelineEvent::agent_rate_limited(
            AgentRole::Developer,
            AgentName::from("agent1"),
            Some("preserved prompt".to_string()),
        ),
    );

    assert_eq!(
        after_rate_limit.agent_chain.rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "preserved prompt".to_string(),
        }),
        "RateLimited should preserve prompt context"
    );

    // Test 2: AuthFailed does NOT set prompt (credentials issue, not exhaustion)
    let state2 = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: base_chain,
        ..PipelineState::initial(5, 2)
    };

    let after_auth = reduce(
        state2,
        PipelineEvent::agent_auth_failed(AgentRole::Developer, AgentName::from("agent1")),
    );

    assert!(
        after_auth
            .agent_chain
            .rate_limit_continuation_prompt
            .is_none(),
        "AuthFailed should not set prompt context (credentials issue, not exhaustion)"
    );
}

#[test]
fn test_timeout_preserves_rate_limit_continuation_prompt_during_same_agent_retry_and_fallback() {
    // Regression: if a prior 429 stored a continuation prompt, subsequent non-rate-limit failures
    // (like timeouts) must preserve that prompt context for retries and fallback so we keep
    // "continue same prompt after 429" semantics across transient failures.
    let base_state = create_test_state();
    let base_chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );
    let chain = AgentChainState {
        rate_limit_continuation_prompt: Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        }),
        ..base_chain
    };

    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: chain,
        continuation: ContinuationState::with_limits(3, 2),
        ..base_state
    };

    let after_first_timeout = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            AgentName::from("agent1"),
            TimeoutOutputKind::PartialResult,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );
    assert_eq!(
        after_first_timeout
            .agent_chain
            .rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        }),
        "Timeout retry must preserve rate-limit continuation prompt context"
    );

    let after_second_timeout = reduce(
        after_first_timeout,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            AgentName::from("agent1"),
            TimeoutOutputKind::PartialResult,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );
    assert_eq!(
        after_second_timeout.agent_chain.rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        }),
        "Timeout fallback after budget exhaustion must preserve rate-limit continuation prompt for the next agent"
    );
}

#[test]
fn test_internal_error_preserves_rate_limit_continuation_prompt_during_same_agent_retry_and_fallback(
) {
    // Same regression as above, but via the InvocationFailed { retriable: false, InternalError }
    // path which uses the same-agent retry mechanism.
    let base_state = create_test_state();
    let base_chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec![], vec![]],
        AgentRole::Developer,
    );
    let chain = AgentChainState {
        rate_limit_continuation_prompt: Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        }),
        ..base_chain
    };

    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: chain,
        continuation: ContinuationState::with_limits(3, 2),
        ..base_state
    };

    let after_first_failure = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            AgentName::from("agent1"),
            139,
            AgentErrorKind::InternalError,
            false,
        ),
    );
    assert_eq!(
        after_first_failure
            .agent_chain
            .rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        }),
        "InternalError retry must preserve rate-limit continuation prompt context"
    );

    let after_second_failure = reduce(
        after_first_failure,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            AgentName::from("agent1"),
            139,
            AgentErrorKind::InternalError,
            false,
        ),
    );
    assert_eq!(
        after_second_failure.agent_chain.rate_limit_continuation_prompt,
        Some(RateLimitContinuationPrompt {
            drain: crate::agents::AgentDrain::Development,
            role: AgentRole::Developer,
            prompt: "saved prompt".to_string(),
        }),
        "InternalError fallback after budget exhaustion must preserve rate-limit continuation prompt for the next agent"
    );
}

#[test]
fn test_timeout_retries_same_agent_until_retry_budget_exhausted() {
    // Desired behavior: timeouts should retry the same agent first and only switch
    // agents after the retry budget is exhausted.
    let base_state = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        continuation: ContinuationState::with_limits(3, 2),
        ..base_state
    };

    let after_first_timeout = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            AgentName::from("agent1"),
            TimeoutOutputKind::PartialResult,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    assert_eq!(
        after_first_timeout
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent1"),
        "First timeout should retry same agent, not immediately fall back"
    );
    assert_eq!(
        after_first_timeout.continuation.same_agent_retry_count, 1,
        "Timeout should consume same-agent retry budget deterministically"
    );
    assert!(
        after_first_timeout.continuation.same_agent_retry_pending,
        "Timeout retry should set same_agent_retry_pending so orchestration can select retry prompt mode"
    );
    assert_eq!(
        after_first_timeout.continuation.same_agent_retry_reason,
        Some(SameAgentRetryReason::TimeoutWithContext)
    );

    let after_second_timeout = reduce(
        after_first_timeout,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            AgentName::from("agent1"),
            TimeoutOutputKind::PartialResult,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    assert_eq!(
        after_second_timeout
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent2"),
        "After exhausting retry budget, timeout should fall back to next agent"
    );
    assert_eq!(after_second_timeout.continuation.same_agent_retry_count, 0);
    assert!(!after_second_timeout.continuation.same_agent_retry_pending);
    assert!(after_second_timeout
        .continuation
        .same_agent_retry_reason
        .is_none());
}

#[test]
fn test_internal_error_retries_same_agent_until_retry_budget_exhausted() {
    // Desired behavior: internal/unknown errors should retry same agent first,
    // only falling back after the configured retry budget is exhausted.
    let base_state = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        continuation: ContinuationState::with_limits(3, 2),
        ..base_state
    };

    let after_first_failure = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            AgentName::from("agent1"),
            139,
            AgentErrorKind::InternalError,
            false,
        ),
    );

    assert_eq!(
        after_first_failure
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent1"),
        "First internal error should retry same agent, not immediately fall back"
    );
    assert_eq!(after_first_failure.continuation.same_agent_retry_count, 1);
    assert!(after_first_failure.continuation.same_agent_retry_pending);
    assert_eq!(
        after_first_failure.continuation.same_agent_retry_reason,
        Some(SameAgentRetryReason::InternalError)
    );

    let after_second_failure = reduce(
        after_first_failure,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            AgentName::from("agent1"),
            139,
            AgentErrorKind::InternalError,
            false,
        ),
    );

    assert_eq!(
        after_second_failure
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent2"),
        "After exhausting retry budget, internal error should fall back to next agent"
    );
    assert_eq!(after_second_failure.continuation.same_agent_retry_count, 0);
    assert!(!after_second_failure.continuation.same_agent_retry_pending);
    assert!(after_second_failure
        .continuation
        .same_agent_retry_reason
        .is_none());
}

#[test]
fn test_non_auth_non_rate_limit_non_retriable_error_retries_same_agent_until_budget_exhausted() {
    // Acceptance: immediate agent fallback happens only for rate limit (429) and auth failures.
    // Other non-retriable errors should retry the same agent first, then fall back after budget.
    let base_state = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        continuation: ContinuationState::with_limits(3, 2),
        ..base_state
    };

    let after_first_failure = reduce(
        state,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            AgentName::from("agent1"),
            1,
            AgentErrorKind::ParsingError,
            false,
        ),
    );

    assert_eq!(
        after_first_failure
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent1"),
        "First non-auth non-rate-limit non-retriable error should retry same agent"
    );
    assert_eq!(after_first_failure.continuation.same_agent_retry_count, 1);
    assert!(after_first_failure.continuation.same_agent_retry_pending);

    let after_second_failure = reduce(
        after_first_failure,
        PipelineEvent::agent_invocation_failed(
            AgentRole::Developer,
            AgentName::from("agent1"),
            1,
            AgentErrorKind::ParsingError,
            false,
        ),
    );

    assert_eq!(
        after_second_failure
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent2"),
        "After exhausting retry budget, non-retriable error should fall back to next agent"
    );
    assert_eq!(after_second_failure.continuation.same_agent_retry_count, 0);
    assert!(!after_second_failure.continuation.same_agent_retry_pending);
    assert!(after_second_failure
        .continuation
        .same_agent_retry_reason
        .is_none());
}

#[test]
fn test_template_variables_invalid_retries_same_agent_until_budget_exhausted() {
    // Acceptance: template errors should not cause immediate agent fallback; retry same agent first.
    let base_state = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        continuation: ContinuationState::with_limits(3, 2),
        ..base_state
    };

    let after_first_invalid = reduce(
        state,
        PipelineEvent::agent_template_variables_invalid(
            AgentRole::Developer,
            "template".to_string(),
            vec!["MISSING".to_string()],
            vec!["{{UNRESOLVED}}".to_string()],
        ),
    );

    assert_eq!(
        after_first_invalid
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent1"),
        "First TemplateVariablesInvalid should retry same agent, not immediately fall back"
    );
    assert_eq!(after_first_invalid.continuation.same_agent_retry_count, 1);
    assert!(after_first_invalid.continuation.same_agent_retry_pending);

    let after_second_invalid = reduce(
        after_first_invalid,
        PipelineEvent::agent_template_variables_invalid(
            AgentRole::Developer,
            "template".to_string(),
            vec!["MISSING".to_string()],
            vec!["{{UNRESOLVED}}".to_string()],
        ),
    );

    assert_eq!(
        after_second_invalid
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent2"),
        "After exhausting retry budget, TemplateVariablesInvalid should fall back to next agent"
    );
    assert_eq!(after_second_invalid.continuation.same_agent_retry_count, 0);
    assert!(!after_second_invalid.continuation.same_agent_retry_pending);
}

// ========================================================================
// NoResult timeout immediate switch tests (AC-3, AC-4)
// ========================================================================

#[test]
fn test_no_output_timeout_triggers_immediate_agent_switch() {
    // AC-3: NoResult timeout should switch agents immediately without same-agent retry
    let base_state = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        continuation: ContinuationState::with_limits(3, 2),
        ..base_state
    };

    let after_timeout = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            AgentName::from("agent1"),
            TimeoutOutputKind::NoResult,
            None,
            None,
        ),
    );

    // Immediate switch to next agent
    assert_eq!(
        after_timeout
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent2"),
        "NoResult timeout should immediately switch to next agent"
    );
    // Retry budget NOT consumed
    assert_eq!(
        after_timeout.continuation.same_agent_retry_count, 0,
        "NoResult timeout should not consume retry budget"
    );
    assert!(
        !after_timeout.continuation.same_agent_retry_pending,
        "NoResult timeout should not set same_agent_retry_pending"
    );
    // Session cleared
    assert!(
        after_timeout.agent_chain.last_session_id.is_none(),
        "NoResult timeout should clear session ID"
    );
    // Metric incremented
    assert_eq!(
        after_timeout.metrics.timeout_no_output_agent_switches_total, 1,
        "NoResult timeout should increment timeout_no_output_agent_switches_total"
    );
    // same_agent_retry_attempts_total NOT incremented
    assert_eq!(
        after_timeout.metrics.same_agent_retry_attempts_total, 0,
        "NoResult timeout should not increment same_agent_retry_attempts_total"
    );
}

#[test]
fn test_no_output_timeout_does_not_consume_retry_budget() {
    // AC-4: Starting with same_agent_retry_count = 1, NoResult should still switch immediately
    // and reset count to 0 (not increment to 2)
    let base_state = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        continuation: ContinuationState {
            same_agent_retry_count: 1,
            ..ContinuationState::with_limits(3, 2)
        },
        ..base_state
    };

    let after_timeout = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            AgentName::from("agent1"),
            TimeoutOutputKind::NoResult,
            None,
            None,
        ),
    );

    // Should switch agents immediately
    assert_eq!(
        after_timeout
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent2"),
        "NoResult timeout should switch agents immediately even with prior retry count"
    );
    // Count should be reset to 0, not incremented
    assert_eq!(
        after_timeout.continuation.same_agent_retry_count, 0,
        "NoResult timeout should reset retry count to 0, not increment"
    );
}

// ========================================================================
// PartialResult vs NoResult explicit contrast tests
// ========================================================================

/// PartialResult timeout on first occurrence keeps the same agent (does not switch).
///
/// This is the critical distinction from NoResult: PartialResult means the agent produced
/// some output before timing out (likely a connectivity issue), so it gets a same-agent
/// retry first. NoResult switches immediately.
#[test]
fn test_partial_result_timeout_first_occurrence_stays_same_agent() {
    let base_state = create_test_state();
    let state = PipelineState {
        phase: PipelinePhase::Development,
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec![], vec![]],
            AgentRole::Developer,
        ),
        continuation: ContinuationState::with_limits(3, 2),
        ..base_state
    };

    let after_timeout = reduce(
        state,
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            AgentName::from("agent1"),
            TimeoutOutputKind::PartialResult,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    // PartialResult: same agent on first timeout
    assert_eq!(
        after_timeout
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent1"),
        "PartialResult first timeout must keep same agent (not switch to agent2)"
    );
    assert_eq!(
        after_timeout.continuation.same_agent_retry_count, 1,
        "PartialResult timeout must consume one same-agent retry"
    );
    assert!(
        after_timeout.continuation.same_agent_retry_pending,
        "PartialResult timeout must set same_agent_retry_pending"
    );
    // timeout_no_output_agent_switches_total must NOT be incremented for PartialResult
    assert_eq!(
        after_timeout.metrics.timeout_no_output_agent_switches_total, 0,
        "PartialResult timeout must not increment timeout_no_output_agent_switches_total"
    );
    // same_agent_retry_attempts_total MUST be incremented
    assert_eq!(
        after_timeout.metrics.same_agent_retry_attempts_total, 1,
        "PartialResult timeout must increment same_agent_retry_attempts_total"
    );
}

/// Side-by-side contrast: same state, PartialResult stays on same agent while
/// NoResult switches immediately. This documents the classification contract.
///
/// | Condition            | Agent switch? | Retry budget consumed? | Metric incremented?               |
/// |----------------------|---------------|------------------------|-----------------------------------|
/// | PartialResult        | No (retry)    | Yes (+1)               | same_agent_retry_attempts_total   |
/// | NoResult             | Yes (switch)  | No (reset to 0)        | timeout_no_output_agent_switches_total |
#[test]
fn test_partial_result_vs_no_result_timeout_classification_contract() {
    let make_state = || {
        let base_state = create_test_state();
        PipelineState {
            phase: PipelinePhase::Development,
            agent_chain: AgentChainState::initial().with_agents(
                vec!["agent1".to_string(), "agent2".to_string()],
                vec![vec![], vec![]],
                AgentRole::Developer,
            ),
            continuation: ContinuationState::with_limits(3, 2),
            ..base_state
        }
    };

    let after_partial = reduce(
        make_state(),
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            AgentName::from("agent1"),
            TimeoutOutputKind::PartialResult,
            Some(".agent/logs/developer_0.log".to_string()),
            None,
        ),
    );

    let after_no_result = reduce(
        make_state(),
        PipelineEvent::agent_timed_out(
            AgentRole::Developer,
            AgentName::from("agent1"),
            TimeoutOutputKind::NoResult,
            None,
            None,
        ),
    );

    // PartialResult: stays same agent, consumes retry budget
    assert_eq!(
        after_partial
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent1"),
        "PartialResult must stay on same agent"
    );
    assert_eq!(
        after_partial.continuation.same_agent_retry_count, 1,
        "PartialResult must increment retry count"
    );
    assert_eq!(
        after_partial.metrics.same_agent_retry_attempts_total, 1,
        "PartialResult must increment same_agent_retry_attempts_total"
    );
    assert_eq!(
        after_partial.metrics.timeout_no_output_agent_switches_total, 0,
        "PartialResult must NOT increment timeout_no_output_agent_switches_total"
    );

    // NoResult: switches to next agent, does NOT consume retry budget
    assert_eq!(
        after_no_result
            .agent_chain
            .current_agent()
            .map(String::as_str),
        Some("agent2"),
        "NoResult must switch to next agent"
    );
    assert_eq!(
        after_no_result.continuation.same_agent_retry_count, 0,
        "NoResult must reset retry count to 0"
    );
    assert_eq!(
        after_no_result.metrics.same_agent_retry_attempts_total, 0,
        "NoResult must NOT increment same_agent_retry_attempts_total"
    );
    assert_eq!(
        after_no_result
            .metrics
            .timeout_no_output_agent_switches_total,
        1,
        "NoResult must increment timeout_no_output_agent_switches_total"
    );
}
