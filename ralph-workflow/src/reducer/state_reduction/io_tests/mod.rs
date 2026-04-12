mod agent_fallback;
mod awaiting_dev_fix;
mod basic_transitions;
mod cloud_push_retry;
mod commit_phase;
mod connectivity;
mod continuation;
mod dev_review_transition;
mod event_sequence;
mod finalization;
mod fix_continuation;
mod gitignore_entries;
mod metrics;
mod output_validation;
mod prompt_permissions;
mod proptest_reducers;
mod rebase_commit;
mod review_phase;
mod template_validation;

pub use crate::agents::AgentRole;
pub use crate::common::domain_types::AgentName;
pub use crate::reducer::effect::Effect;
pub use crate::reducer::event::AgentErrorKind;
pub use crate::reducer::event::{AgentEvent, PipelineEvent, PipelinePhase};
pub use crate::reducer::io_tests::create_test_state;
pub use crate::reducer::orchestration::determine_next_effect;
pub use crate::reducer::state::{
    AgentChainState, CommitState, ConnectivityState, ContinuationState, PipelineState, RunMetrics,
    SameAgentRetryReason,
};
pub(super) use crate::reducer::state_reduction::reduce;
