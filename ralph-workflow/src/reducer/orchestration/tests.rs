// Orchestration tests for pipeline phase transitions.
//
// Tests for effect determination and phase transitions across all pipeline phases.

use super::*;
use crate::reducer::state::AgentChainState;
use crate::reducer::{reduce, PipelineEvent};

#[must_use]
fn create_test_state() -> PipelineState {
    PipelineState {
        // Set locked=true so tests don't need to deal with LockPromptPermissions effect
        prompt_permissions: crate::reducer::state::PromptPermissionsState {
            locked: true,
            restore_needed: true,
            restored: false,
            last_warning: None,
        },
        ..PipelineState::initial(5, 2)
    }
}

// Interrupted phase checkpoint behavior tests
#[path = "io_tests/interrupted_phase.rs"]
mod interrupted_phase;

#[path = "io_tests/planning_phase.rs"]
mod planning_phase;

#[path = "io_tests/development_phase.rs"]
mod development_phase;

#[path = "io_tests/review_phase.rs"]
mod review_phase;

#[path = "io_tests/commit_phase.rs"]
mod commit_phase;

#[path = "io_tests/pipeline_flow.rs"]
mod pipeline_flow;

#[path = "io_tests/retry_cleans_xml.rs"]
mod retry_cleans_xml;

#[path = "io_tests/resume_boundary.rs"]
mod resume_boundary;

#[path = "io_tests/prompt_permissions.rs"]
mod prompt_permissions;

#[path = "io_tests/recovery_flow.rs"]
mod recovery_flow;

#[path = "io_tests/cloud_push.rs"]
mod cloud_push;
