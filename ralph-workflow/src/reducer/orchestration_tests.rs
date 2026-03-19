//! Comprehensive orchestration tests for pipeline phase transitions.
//!
//! This module contains systematic tests for ALL phase transitions and state management
//! in the reducer-based pipeline architecture. These tests verify that:
//! - Each phase correctly determines the next effect based on state
//! - State transitions happen correctly when events are applied
//! - Iteration/pass counts are respected (no off-by-one errors)
//! - Phase transitions occur at the right time
//! - The complete pipeline flows from Planning → Development → Review → Commit → Complete

use super::orchestration::determine_next_effect;
use super::state_reduction::reduce;
use crate::agents::AgentRole;
use crate::reducer::effect::Effect;
use crate::reducer::event::{DevelopmentEvent, PipelineEvent, PipelinePhase};
use crate::reducer::state::{PipelineState, PromptMode};

#[must_use]
fn create_test_state() -> PipelineState {
    let state = PipelineState::initial(5, 2);
    let state = {
        let mut s = state;
        s.prompt_permissions.locked = true;
        s.prompt_permissions.restore_needed = true;
        s
    };
    state
}

/// Helper to create initial state with locked permissions (for mid-pipeline test scenarios)
#[must_use]
fn initial_with_locked_permissions(dev_iters: u32, review_passes: u32) -> PipelineState {
    let state = PipelineState::initial(dev_iters, review_passes);
    let state = {
        let mut s = state;
        s.prompt_permissions.locked = true;
        s.prompt_permissions.restore_needed = true;
        s
    };
    state
}

// Review phase single-task effect chain tests
#[path = "orchestration_io_tests/review_phase_effects.rs"]
mod review_phase_effects;

#[path = "orchestration_io_tests/fix_chain_effects.rs"]
mod fix_chain_effects;

#[path = "orchestration_io_tests/planning_phase.rs"]
mod planning_phase;

#[path = "orchestration_io_tests/development_phase.rs"]
mod development_phase;

#[path = "orchestration_io_tests/review_phase.rs"]
mod review_phase;

#[path = "orchestration_io_tests/commit_phase.rs"]
mod commit_phase;

#[path = "orchestration_io_tests/pipeline_flow.rs"]
mod pipeline_flow;
