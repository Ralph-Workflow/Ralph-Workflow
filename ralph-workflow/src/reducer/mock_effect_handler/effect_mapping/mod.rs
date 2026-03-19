//! Effect-to-event mapping for `MockEffectHandler`.
//!
//! This module coordinates effect execution by delegating to phase-specific modules.
//! It contains the main `execute_mock` method that routes effects to the appropriate handler.
//!
//! ## Purpose
//!
//! The `execute_mock` method provides deterministic, hermetic testing of:
//! - Reducer state transitions (given events → new state)
//! - Orchestrator decisions (given state → next effect)
//! - Event loop behavior (orchestrate → execute → reduce cycle)
//!
//! ## Module Organization
//!
//! Effect handling is split by pipeline phase for maintainability:
//!
//! - [`planning_effects`] - Planning phase (generate implementation plan)
//! - [`development_effects`] - Development phase (implement changes iteratively)
//! - [`review_effects`] - Review phase (analyze changes, find issues, fix them)
//! - [`commit_effects`] - Commit phase (generate commit message, create commit)
//! - [`lifecycle_effects`] - Lifecycle effects (checkpointing, agent management, finalization)
//!
//! ## Design
//!
//! Each phase-specific module provides a `handle_*_effect` method that:
//! - Returns `Some((event, ui_events))` if it handles the effect
//! - Returns `None` if the effect doesn't belong to that phase
//!
//! The main `execute_mock` method tries each phase handler in sequence until one
//! matches, or panics if no handler matched (indicating an unhandled effect type).
//!
//! ## Special Cases
//!
//! Effects requiring workspace access (`SaveCheckpoint`, `TriggerDevFixFlow`)
//! are NOT fully handled here - they're handled in the `EffectHandler::execute()`
//! trait implementation which has access to `PhaseContext`.
//!
//! ## See Also
//!
//! - [`super::handler`] - `EffectHandler` trait implementation with workspace access
//! - [`super::core`] - `MockEffectHandler` struct and builder methods

use super::{Effect, EffectResult, MockEffectHandler};

// Phase-specific effect handlers
// Each module provides impl blocks extending MockEffectHandler
mod commit_effects;
mod development_effects;
mod lifecycle_effects;
mod planning_effects;
mod review_effects;

impl MockEffectHandler {
    /// Execute an effect without requiring `PhaseContext`.
    ///
    /// This is used for testing when you don't have a full `PhaseContext`.
    /// It captures the effect and returns an appropriate mock `EffectResult`.
    ///
    /// Most effects are handled here with pure effect-to-event mapping.
    /// Effects requiring workspace access (`SaveCheckpoint`, `TriggerDevFixFlow`)
    /// panic and must be called via `execute()` instead.
    ///
    /// ## Implementation Strategy
    ///
    /// This method delegates to phase-specific handlers in sequence:
    /// 1. Try lifecycle effects (agent management, checkpointing, finalization)
    /// 2. Try planning effects
    /// 3. Try development effects
    /// 4. Try review effects (includes fix phase)
    /// 5. Try commit effects (includes rebase)
    /// 6. Panic if no handler matched (unhandled effect type)
    ///
    /// Each phase handler returns `Some(...)` if it handled the effect,
    /// or `None` to try the next handler.
    ///
    /// # Panics
    ///
    /// Panics if invariants are violated.
    pub fn execute_mock(&mut self, effect: &Effect) -> EffectResult {
        self.captured_state.push_effect(effect.clone());

        if let Some((event, ui_events, additional_events)) =
            self.handle_lifecycle_effect(effect.clone())
        {
            ui_events
                .iter()
                .for_each(|e| self.captured_state.push_ui_event(e.clone()));

            self.captured_state.push_event(event.clone());
            additional_events
                .iter()
                .for_each(|e| self.captured_state.push_event(e.clone()));

            return EffectResult {
                event,
                additional_events,
                ui_events,
            };
        }

        let (event, ui_events) = self
            .handle_planning_effect(effect)
            .or_else(|| self.handle_development_effect(effect))
            .or_else(|| self.handle_review_effect(effect))
            .or_else(|| Self::handle_fix_effect(effect))
            .or_else(|| self.handle_commit_effect(effect.clone()))
            .unwrap_or_else(|| {
                panic!("MockEffectHandler::execute_mock received unhandled effect: {effect:?}")
            });

        ui_events
            .iter()
            .for_each(|e| self.captured_state.push_ui_event(e.clone()));

        self.captured_state.push_event(event.clone());

        EffectResult {
            event,
            additional_events: Vec::new(),
            ui_events,
        }
    }
}
