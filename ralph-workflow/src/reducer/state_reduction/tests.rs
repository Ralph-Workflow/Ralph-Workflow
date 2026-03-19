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
    let state = PipelineState::initial(5, 2);
    let state = {
        let mut s = state;
        s.agent_chain = AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec!["model1".to_string(), "model2".to_string()]],
            AgentRole::Developer,
        );
        s.prompt_permissions.locked = true;
        s.prompt_permissions.restore_needed = true;
        s
    };
    state
}

// Review phase started tests
#[path = "io_tests/review_phase.rs"]
mod review_phase;

#[path = "io_tests/basic_transitions.rs"]
mod basic_transitions;

#[path = "io_tests/agent_fallback.rs"]
mod agent_fallback;

#[path = "io_tests/rebase_commit.rs"]
mod rebase_commit;

#[path = "io_tests/finalization.rs"]
mod finalization;

#[path = "io_tests/continuation.rs"]
mod continuation;

#[path = "io_tests/output_validation.rs"]
mod output_validation;

#[path = "io_tests/event_sequence.rs"]
mod event_sequence;

#[path = "io_tests/dev_review_transition.rs"]
mod dev_review_transition;

#[path = "io_tests/xsd_retry/mod.rs"]
mod xsd_retry;

#[path = "io_tests/fix_continuation.rs"]
mod fix_continuation;

#[path = "io_tests/metrics/mod.rs"]
mod metrics;

#[path = "io_tests/gitignore_entries.rs"]
mod gitignore_entries;

#[path = "io_tests/prompt_permissions.rs"]
mod prompt_permissions;

#[path = "io_tests/template_validation.rs"]
mod template_validation;

#[path = "io_tests/awaiting_dev_fix.rs"]
mod awaiting_dev_fix;

#[path = "io_tests/commit_phase.rs"]
mod commit_phase;

#[path = "io_tests/cloud_push_retry.rs"]
mod cloud_push_retry;
