// Tests for MockEffectHandler.
//
// This module contains all test code for the mock effect handler module.

// Re-export test submodules
mod assertion_helpers;
mod effect_execution;
mod expectation_matching;

// Common imports used by all test submodules
pub(super) use crate::reducer::effect::{Effect, EffectHandler};
pub(super) use crate::reducer::event::{PipelineEvent, PipelinePhase};
pub(super) use crate::reducer::mock_effect_handler::MockEffectHandler;
pub(super) use crate::reducer::state::PipelineState;
pub(super) use crate::reducer::ui_event::{UIEvent, XmlOutputType};
