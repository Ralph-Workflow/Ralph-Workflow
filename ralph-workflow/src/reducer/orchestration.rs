//! Orchestration logic for determining next effect.
//!
//! This module implements the **pure orchestration layer** that derives effects from state.
//! The orchestrator is a critical component of the reducer architecture that bridges
//! state transitions with effect execution.
//!
//! # Pure Function Contract
//!
//! All orchestration functions are **PURE**:
//! - Input: `&PipelineState` (immutable reference to current state)
//! - Output: `Effect` (intention to perform side effects)
//! - No I/O operations (no filesystem, network, environment access)
//! - No side effects (no logging, no mutations, no hidden state)
//! - Deterministic: same state always produces same effect
//!
//! # Architecture Flow
//!
//! ```text
//! State → determine_next_effect() → Effect → Handler → Event → Reducer → State
//!         ^^^^^^^^^^^^^^^^^^^^^^
//!         Pure orchestration (this module)
//! ```
//!
//! The orchestrator examines state and derives the next effect:
//! 1. Check for pending recovery operations (continuation cleanup, loop recovery)
//! 2. Check for retry/fallback conditions (XSD retry, agent retry)
//! 3. Determine normal phase progression effect
//!
//! # Decision Priority
//!
//! Orchestration checks conditions in priority order:
//! 1. **Recovery**: Continuation cleanup, loop recovery
//! 2. **Retry**: XSD retry pending, same-agent retry pending
//! 3. **Continuation**: Agent requested continuation
//! 4. **Normal**: Phase-specific progression
//! 5. **Transition**: Advance to next phase
//!
//! # Testing Strategy
//!
//! Orchestrators are pure functions - test them without mocks:
//!
//! ```ignore
//! #[test]
//! fn test_xsd_retry_pending_derives_cleanup_effect() {
//!     let state = PipelineState {
//!         continuation: ContinuationState {
//!             xsd_retry_pending: true,
//!             ..Default::default()
//!         },
//!         ..test_state()
//!     };
//!
//!     let effect = determine_next_effect(&state);
//!
//!     assert!(matches!(effect, Effect::CleanupRequiredFiles { files } if files.iter().any(|f| f.contains("development_result.xml")));
//! }
//! ```
//!
//! See [`tests`] module for comprehensive orchestration tests.

use super::event::{CheckpointTrigger, PipelinePhase, RebasePhase};
use super::state::{CommitState, PipelineState, PromptMode, RebaseState};

#[cfg(test)]
use crate::agents::AgentRole;
use crate::reducer::effect::{ContinuationContextData, Effect};

mod phase_effects;
use phase_effects::determine_next_effect_for_phase;

include!("orchestration/xsd_retry.rs");

/// Returns true if recovery state is active (dev-fix occurred and we transitioned back).
///
/// Recovery state is considered active when:
/// - `dev_fix_attempt_count` > 0 (at least one recovery attempt)
/// - `recovery_escalation_level` > 0 (escalation level set)
/// - `previous_phase` is `AwaitingDevFix` (just transitioned back from recovery)
///
/// When recovery state is active and a phase completes successfully (e.g., Planning
/// validates, Development completes), the orchestration should emit `RecoverySucceeded`
/// to clear the recovery tracking fields and resume normal operation.
pub(in crate::reducer::orchestration) const fn is_recovery_state_active(
    state: &PipelineState,
) -> bool {
    state.dev_fix_attempt_count > 0
        && state.recovery_escalation_level > 0
        && matches!(state.previous_phase, Some(PipelinePhase::AwaitingDevFix))
}

#[cfg(test)]
#[path = "orchestration/io_tests/mod.rs"]
mod tests;
