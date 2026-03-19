// NOTE: split from reducer/state_reduction.rs.

use crate::reducer::event::LifecycleEvent;
use crate::reducer::state::PipelineState;

pub(super) fn reduce_lifecycle_event(
    state: PipelineState,
    event: &LifecycleEvent,
) -> PipelineState {
    match event {
        LifecycleEvent::Started => state,
        LifecycleEvent::Resumed { .. } => PipelineState {
            // A resumed run is active again; do not keep treating it as a user-interrupted
            // termination path. This flag is used ONLY to exempt Ctrl+C termination from the
            // pre-termination commit safety check.
            interrupted_by_user: false,
            ..state
        },
        LifecycleEvent::Completed => PipelineState {
            phase: crate::reducer::event::PipelinePhase::Complete,
            ..state
        },
        LifecycleEvent::GitignoreEntriesEnsured { .. } => {
            // Set flag to prevent re-running effect
            PipelineState {
                gitignore_entries_ensured: true,
                ..state
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resumed_clears_user_interrupt_flag_for_resumed_run() {
        let state = PipelineState::initial(1, 0);
        let state = {
            let mut s = state;
            s.interrupted_by_user = true;
            s
        };

        let reduced = reduce_lifecycle_event(
            state,
            &LifecycleEvent::Resumed {
                from_checkpoint: true,
            },
        );

        assert!(
            !reduced.interrupted_by_user,
            "Resumed runs must clear interrupted_by_user so termination safety checks behave correctly"
        );
    }
}
