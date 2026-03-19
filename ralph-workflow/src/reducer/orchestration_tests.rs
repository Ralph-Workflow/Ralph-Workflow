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
use crate::reducer::state::{AgentChainState, PipelineState, PromptMode, PromptPermissionsState};

#[must_use]
fn create_test_state() -> PipelineState {
    PipelineState {
        agent_chain: AgentChainState::initial().with_agents(
            vec!["agent1".to_string(), "agent2".to_string()],
            vec![vec!["model1".to_string(), "model2".to_string()]],
            AgentRole::Developer,
        ),
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..PipelineState::initial(5, 2)
    }
}

/// Helper to create initial state with locked permissions (for mid-pipeline test scenarios)
#[must_use]
fn initial_with_locked_permissions(dev_iters: u32, review_passes: u32) -> PipelineState {
    PipelineState {
        prompt_permissions: PromptPermissionsState {
            locked: true,
            restore_needed: true,
            ..Default::default()
        },
        ..PipelineState::initial(dev_iters, review_passes)
    }
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
