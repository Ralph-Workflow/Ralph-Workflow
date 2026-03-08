// State reduction tests.
//
// Split into phase-specific test modules for maintainability.

use crate::agents::AgentRole;
use crate::reducer::event::{
    AgentErrorKind, CommitEvent, PipelineEvent, PipelinePhase, RebasePhase,
};
use crate::reducer::state::{
    AgentChainState, CommitState, ContinuationState, PipelineState, RebaseState,
    SameAgentRetryReason,
};

#[must_use]
fn reduce(state: PipelineState, event: PipelineEvent) -> PipelineState {
    crate::reducer::reduce(state, event)
}

#[must_use]
fn create_test_state() -> PipelineState {
    let mut state = PipelineState::initial(5, 2);
    state.agent_chain = AgentChainState::initial().with_agents(
        vec!["agent1".to_string(), "agent2".to_string()],
        vec![vec!["model1".to_string(), "model2".to_string()]],
        AgentRole::Developer,
    );
    // Tests typically simulate mid-pipeline scenarios
    state.prompt_permissions.locked = true;
    state.prompt_permissions.restore_needed = true;
    state
}

// Review phase started tests
#[path = "tests/review_phase.rs"]
mod review_phase;

// Basic pipeline transition tests
#[path = "tests/basic_transitions.rs"]
mod basic_transitions;

// Agent fallback and rate limit tests
#[path = "tests/agent_fallback.rs"]
mod agent_fallback;

// Rebase and commit state machine tests
#[path = "tests/rebase_commit.rs"]
mod rebase_commit;

// Finalization flow tests
#[path = "tests/finalization.rs"]
mod finalization;

// Continuation event handling tests
#[path = "tests/continuation.rs"]
mod continuation;

// Output validation failed tests
#[path = "tests/output_validation.rs"]
mod output_validation;

// Event sequence determinism tests
#[path = "tests/event_sequence.rs"]
mod event_sequence;

// Dev->Review transition agent chain tests
#[path = "tests/dev_review_transition.rs"]
mod dev_review_transition;

// XSD retry state transition tests
#[path = "tests/xsd_retry/mod.rs"]
mod xsd_retry;

// Fix continuation tests
#[path = "tests/fix_continuation.rs"]
mod fix_continuation;

// Metrics tracking tests
#[path = "tests/metrics/mod.rs"]
mod metrics;

// Gitignore entries ensured tests
#[path = "tests/gitignore_entries.rs"]
mod gitignore_entries;

// Prompt permissions lifecycle tests
#[path = "tests/prompt_permissions.rs"]
mod prompt_permissions;

// Template rendering substitution log validation tests
#[path = "tests/template_validation.rs"]
mod template_validation;

// AwaitingDevFix recovery escalation tests
#[path = "tests/awaiting_dev_fix.rs"]
mod awaiting_dev_fix;

// Commit phase reducer tests
#[path = "tests/commit_phase.rs"]
mod commit_phase;

// Cloud push retry semantics tests
#[path = "tests/cloud_push_retry.rs"]
mod cloud_push_retry;
