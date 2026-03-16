//! Loop detection and effect fingerprinting.
//!
//! Provides methods for detecting infinite tight loops by tracking consecutive
//! identical effects. When the same effect is executed too many times in a row,
//! the system triggers loop recovery to break the cycle.

use super::state::ContinuationState;

impl ContinuationState {
    /// Update loop detection counters based on the current effect fingerprint.
    ///
    /// This method updates `last_effect_kind` and `consecutive_same_effect_count`
    /// based on whether the current effect fingerprint matches the previous one.
    ///
    /// # Returns
    ///
    /// A new `ContinuationState` with updated loop detection counters.
    ///
    /// # Behavior
    ///
    /// - If `current_fingerprint` equals `last_effect_kind`: increment `consecutive_same_effect_count`
    /// - Otherwise: reset `consecutive_same_effect_count` to 1 and update `last_effect_kind`
    #[must_use]
    pub fn update_loop_detection_counters(mut self, current_fingerprint: String) -> Self {
        if self.last_effect_kind.as_deref() == Some(&current_fingerprint) {
            // Same effect as last time - increment counter
            self.consecutive_same_effect_count =
                self.consecutive_same_effect_count.saturating_add(1);
        } else {
            // Different effect - reset counter and update fingerprint
            self.last_effect_kind = Some(current_fingerprint);
            self.consecutive_same_effect_count = 1;
        }
        self
    }

    /// Check if loop detection threshold has been exceeded.
    ///
    /// Returns `true` if `consecutive_same_effect_count` >= `max_consecutive_same_effect`.
    #[must_use]
    pub const fn is_loop_detected(&self) -> bool {
        self.consecutive_same_effect_count >= self.max_consecutive_same_effect
    }
}
