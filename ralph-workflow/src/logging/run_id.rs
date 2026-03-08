use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::fmt;

/// Unique identifier for a pipeline run.
///
/// Format: YYYY-MM-DD_HH-mm-ss.SSSZ[-NN]
/// where NN is an optional collision counter (01, 02, etc.)
///
/// The format is designed to be:
/// - Human-readable
/// - Machine-sortable (lexicographic sort == chronological order)
/// - Filesystem-safe (no colons, valid on macOS, Linux, Windows)
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RunId(String);

impl RunId {
    /// Generate a new run ID based on current UTC timestamp.
    ///
    /// Returns a `RunId` with format: YYYY-MM-DD_HH-mm-ss.SSSZ
    #[must_use]
    pub fn new() -> Self {
        let now = Utc::now();
        let base = now.format("%Y-%m-%d_%H-%M-%S%.3fZ").to_string();
        Self(base)
    }

    /// Create a `RunId` from a string value (for testing).
    ///
    /// This is a test-only constructor that allows creating a `RunId` with
    /// a fixed timestamp value for deterministic testing of collision handling.
    ///
    /// # Warning
    ///
    /// This is intended for testing only. Using a fixed `run_id` in production
    /// could lead to directory collisions. Always use [`RunId::new`]
    /// or [`RunId::from_checkpoint`] in production code.
    #[must_use]
    pub fn for_test(id: &str) -> Self {
        Self(id.to_string())
    }

    /// Create a `RunId` from an existing string (for resume).
    ///
    /// This is used when loading a checkpoint to continue using
    /// the same `run_id` from the previous session.
    #[must_use]
    pub fn from_checkpoint(id: &str) -> Self {
        Self(id.to_string())
    }

    /// Get the run ID as a string slice.
    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Generate a collision-safe variant with counter suffix.
    ///
    /// Used when the base run directory already exists (rare case
    /// of multiple runs starting in the same millisecond).
    ///
    /// # Arguments
    /// * `counter` - Collision counter (1-99)
    ///
    /// # Returns
    /// A new `RunId` with format: YYYY-MM-DD_HH-mm-ss.SSSZ-NN
    #[must_use]
    pub fn with_collision_counter(&self, counter: u32) -> Self {
        Self(format!("{}-{:02}", self.0, counter))
    }
}

impl fmt::Display for RunId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl Default for RunId {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_run_id_format() {
        let run_id = RunId::new();
        let s = run_id.as_str();

        // Check format: YYYY-MM-DD_HH-mm-ss.SSSZ
        // Should be 24 characters for base format
        assert!(s.len() >= 24, "Run ID should be at least 24 chars");
        assert!(s.ends_with('Z'), "Run ID should end with Z");
        assert!(
            s.contains('_'),
            "Run ID should contain underscore separator"
        );
        assert!(
            s.contains('-'),
            "Run ID should contain date/time separators"
        );
        assert!(
            s.contains('.'),
            "Run ID should contain millisecond separator"
        );
    }

    #[test]
    fn test_run_id_from_checkpoint() {
        let original = "2026-02-06_14-03-27.123Z";
        let run_id = RunId::from_checkpoint(original);
        assert_eq!(run_id.as_str(), original);
    }

    #[test]
    fn test_run_id_with_collision_counter() {
        let base = RunId::new();
        let collided = base.with_collision_counter(1);

        assert!(
            collided.as_str().ends_with("-01"),
            "Collision counter should be appended"
        );
        assert!(collided.as_str().starts_with(base.as_str()));
    }

    #[test]
    fn test_run_id_display() {
        let run_id = RunId::new();
        let displayed = format!("{run_id}");
        assert_eq!(displayed, run_id.as_str());
    }

    #[test]
    fn test_run_id_sortable() {
        // The RunId format (YYYY-MM-DD_HH-mm-ss.SSSZ) is designed so that
        // lexicographic order equals chronological order.
        // Verify the format property with two known-ordered strings — no real clock needed.
        let earlier = RunId::for_test("2024-01-01_00-00-01.000Z");
        let later = RunId::for_test("2024-01-01_00-00-02.000Z");
        assert!(
            earlier.as_str() < later.as_str(),
            "RunId format should be lexicographically sortable in chronological order"
        );
    }
}
