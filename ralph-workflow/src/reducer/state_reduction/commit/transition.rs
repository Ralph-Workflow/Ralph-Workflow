use crate::reducer::state::PipelineState;

/// Compute phase transition after a commit (used by `CommitCreated` and `CommitSkipped`).
pub(super) const fn compute_post_commit_transition(
    state: &PipelineState,
) -> (crate::reducer::event::PipelinePhase, u32, u32) {
    match state.previous_phase {
        Some(crate::reducer::event::PipelinePhase::Development) => {
            let next_iter = state.iteration + 1;
            if next_iter >= state.total_iterations {
                if state.total_reviewer_passes == 0 {
                    (
                        crate::reducer::event::PipelinePhase::FinalValidation,
                        next_iter,
                        state.reviewer_pass,
                    )
                } else {
                    (
                        crate::reducer::event::PipelinePhase::Review,
                        next_iter,
                        state.reviewer_pass,
                    )
                }
            } else {
                (
                    crate::reducer::event::PipelinePhase::Planning,
                    next_iter,
                    state.reviewer_pass,
                )
            }
        }
        Some(crate::reducer::event::PipelinePhase::Review) => {
            let next_pass = state.reviewer_pass + 1;
            if next_pass < state.total_reviewer_passes {
                // More reviewer passes remain for this iteration — continue reviewing.
                (
                    crate::reducer::event::PipelinePhase::Review,
                    state.iteration,
                    next_pass,
                )
            } else {
                // All reviewer passes done for this iteration.
                // Check if more dev iterations remain (Phase 2: per-iteration review).
                let next_iter = state.iteration + 1;
                if next_iter < state.total_iterations {
                    // More dev iterations remain — return to Planning with reviewer_pass reset.
                    (crate::reducer::event::PipelinePhase::Planning, next_iter, 0)
                } else {
                    // All dev iterations and reviewer passes complete — finalize.
                    (
                        crate::reducer::event::PipelinePhase::FinalValidation,
                        state.iteration,
                        next_pass,
                    )
                }
            }
        }
        _ => (
            crate::reducer::event::PipelinePhase::FinalValidation,
            state.iteration,
            state.reviewer_pass,
        ),
    }
}
