use crate::orchestrator::reduce_review;
use crate::reducer::determine_next_effect;

pub fn boundary_handler(state: &PipelineState) -> Option<Effect> {
    let eff = determine_next_effect(state);
    let _ = reduce_review(state, ReviewEvent::IssueRecorded);
    eff
}
