//! Checkpoint size monitoring and alerting.
//!
//! This module provides size monitoring for checkpoint files to detect
//! and alert on checkpoints that approach or exceed size thresholds.
//!
//! # Thresholds
//!
//! - **Warning threshold**: 1.5 MiB (log warning, continue operation)
//! - **Error threshold**: 2 MiB (hard limit enforced by tests)
//!
//! These thresholds are based on observed checkpoint sizes with bounded
//! execution history (default 1000 entries ≈ 363 KB serialized).

/// Alert level for checkpoint size.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SizeAlert {
    /// Checkpoint size is within acceptable range.
    Ok,
    /// Checkpoint size approaches limit (warning threshold).
    Warning(String),
    /// Checkpoint size exceeds hard limit.
    Error(String),
}

/// Checkpoint size thresholds in bytes.
#[derive(Debug, Clone)]
pub struct SizeThresholds {
    /// Warning threshold in bytes (default: 1.5 MiB)
    pub warn_threshold: usize,
    /// Error threshold in bytes (default: 2 MiB)
    pub error_threshold: usize,
}

impl SizeThresholds {
    /// Default thresholds based on performance baselines.
    ///
    /// # Rationale
    ///
    /// - Default execution history limit: 1000 entries
    /// - Measured checkpoint size: ~363 KB for 1000 entries
    /// - Warning threshold: 1.5 MiB (4x baseline, allows growth headroom)
    /// - Error threshold: 2 MiB (hard limit enforced by CI)
    pub const DEFAULT: Self = Self {
        warn_threshold: 1_572_864,  // 1.5 MiB
        error_threshold: 2_097_152, // 2 MiB
    };

    /// Create custom thresholds.
    #[must_use]
    pub const fn new(warn_threshold: usize, error_threshold: usize) -> Self {
        Self {
            warn_threshold,
            error_threshold,
        }
    }
}

impl Default for SizeThresholds {
    fn default() -> Self {
        Self::DEFAULT
    }
}

/// Checkpoint size monitor.
#[derive(Debug)]
pub struct CheckpointSizeMonitor {
    thresholds: SizeThresholds,
}

impl CheckpointSizeMonitor {
    /// Create a new monitor with default thresholds.
    #[must_use]
    pub const fn new() -> Self {
        Self {
            thresholds: SizeThresholds::DEFAULT,
        }
    }

    /// Create a new monitor with custom thresholds.
    #[must_use]
    pub const fn with_thresholds(thresholds: SizeThresholds) -> Self {
        Self { thresholds }
    }

    /// Check checkpoint size and return appropriate alert.
    #[must_use]
    pub fn check_size(&self, size_bytes: usize) -> SizeAlert {
        if size_bytes >= self.thresholds.error_threshold {
            SizeAlert::Error(format!(
                "Checkpoint size {} bytes exceeds hard limit {} bytes. \
                 Consider reducing execution_history_limit in config.",
                size_bytes, self.thresholds.error_threshold
            ))
        } else if size_bytes >= self.thresholds.warn_threshold {
            let pct_of_error_threshold: u128 = if self.thresholds.error_threshold == 0 {
                100
            } else {
                (size_bytes as u128).saturating_mul(100) / (self.thresholds.error_threshold as u128)
            };
            SizeAlert::Warning(format!(
                "Checkpoint size {} bytes exceeds warning threshold {} bytes; \
                 current size is {}% of hard limit {} bytes.",
                size_bytes,
                self.thresholds.warn_threshold,
                pct_of_error_threshold,
                self.thresholds.error_threshold
            ))
        } else {
            SizeAlert::Ok
        }
    }

    /// Check serialized JSON size and return an alert.
    #[must_use]
    pub fn check_json(&self, json: &str) -> SizeAlert {
        self.check_size(json.len())
    }

    /// Backwards-compatible wrapper.
    ///
    /// Library code must not print directly; callers decide how/where to log.
    #[deprecated(since = "0.7.3", note = "Use check_json(json) and log at the callsite")]
    #[must_use]
    pub fn check_json_and_log(&self, json: &str) -> SizeAlert {
        self.check_json(json)
    }

    /// Get current thresholds.
    #[must_use]
    pub const fn thresholds(&self) -> &SizeThresholds {
        &self.thresholds
    }
}

impl Default for CheckpointSizeMonitor {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_size_alert_ok_for_small_checkpoints() {
        let monitor = CheckpointSizeMonitor::new();
        let alert = monitor.check_size(363_000); // 363 KB (typical size)

        assert_eq!(alert, SizeAlert::Ok);
    }

    #[test]
    fn test_size_alert_warning_approaching_limit() {
        let monitor = CheckpointSizeMonitor::new();
        let alert = monitor.check_size(1_600_000); // 1.6 MB (over warning threshold)

        match alert {
            SizeAlert::Warning(msg) => {
                assert!(msg.contains("1600000"));
                assert!(msg.contains("warning threshold"));
                assert!(msg.contains("hard limit"));
            }
            _ => panic!("Expected Warning, got {alert:?}"),
        }
    }

    #[test]
    fn test_size_alert_error_exceeds_limit() {
        let monitor = CheckpointSizeMonitor::new();
        let alert = monitor.check_size(2_100_000); // 2.1 MB (over error threshold)

        match alert {
            SizeAlert::Error(msg) => {
                assert!(msg.contains("2100000"));
                assert!(msg.contains("exceeds hard limit"));
            }
            _ => panic!("Expected Error, got {alert:?}"),
        }
    }

    #[test]
    fn test_custom_thresholds() {
        let thresholds = SizeThresholds::new(1_000_000, 1_500_000);
        let monitor = CheckpointSizeMonitor::with_thresholds(thresholds);

        // Below warning
        assert_eq!(monitor.check_size(900_000), SizeAlert::Ok);

        // Above warning, below error
        let alert = monitor.check_size(1_100_000);
        assert!(matches!(alert, SizeAlert::Warning(_)));

        // Above error
        let alert = monitor.check_size(1_600_000);
        assert!(matches!(alert, SizeAlert::Error(_)));
    }

    #[test]
    fn test_check_json() {
        let monitor = CheckpointSizeMonitor::new();

        // Small JSON - should return Ok
        let small_json = "x".repeat(100_000); // 100 KB
        let alert = monitor.check_json(&small_json);
        assert_eq!(alert, SizeAlert::Ok);

        // Large JSON - should return Warning
        let large_json = "x".repeat(1_600_000); // 1.6 MB
        let alert = monitor.check_json(&large_json);
        assert!(matches!(alert, SizeAlert::Warning(_)));
    }

    #[test]
    fn test_warning_percentage_calculation_does_not_overflow() {
        // Regression test for overflow in `(size_bytes * 100) / error_threshold`.
        // With large sizes and thresholds, `usize` multiplication can overflow in debug builds.
        let thresholds = SizeThresholds::new(1, usize::MAX);
        let monitor = CheckpointSizeMonitor::with_thresholds(thresholds);

        let result = std::panic::catch_unwind(|| monitor.check_size(usize::MAX - 1));
        assert!(result.is_ok(), "check_size must not panic on large inputs");

        let alert = result.unwrap();
        assert!(matches!(alert, SizeAlert::Warning(_)));
    }

    #[test]
    fn test_thresholds_default() {
        let thresholds = SizeThresholds::default();
        assert_eq!(thresholds.warn_threshold, 1_572_864);
        assert_eq!(thresholds.error_threshold, 2_097_152);
    }

    #[test]
    fn test_monitor_default() {
        let monitor = CheckpointSizeMonitor::default();
        assert_eq!(
            monitor.thresholds().warn_threshold,
            SizeThresholds::DEFAULT.warn_threshold
        );
    }
}
