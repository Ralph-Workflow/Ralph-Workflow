//! Metrics tracking tests
//!
//! Verifies that `RunMetrics` counters increment correctly on reducer events.
//! Tests are split by metric category:
//! - `iteration_tracking` - Development and review iteration counters
//! - `retry_counting` - XSD retry, same-agent retry, and continuation counters
//! - `phase_transitions` - Phase-specific metric updates
//! - `summary_accuracy` - Final metric calculation and summary consistency

mod iteration_tracking;
mod phase_transitions;
mod retry_counting;
mod summary_accuracy;

use crate::agents::AgentRole;
use crate::reducer::event::{
    CommitEvent, DevelopmentEvent, PipelineEvent, PlanningEvent, ReviewEvent, TimeoutOutputKind,
};
use crate::reducer::state::{ArtifactType, DevelopmentStatus, PipelineState};
use crate::reducer::state_reduction::reduce;
