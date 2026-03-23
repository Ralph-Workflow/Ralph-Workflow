//! Production memory profiling and metrics.
//!
//! This module provides lightweight memory usage tracking for production
//! deployments. It enables detection of memory issues without requiring
//! external profiling tools.
//!
//! # Feature Flag
//!
//! This module is only available when the `monitoring` feature is enabled.

pub(super) mod backends;
pub(super) mod collector;
pub(super) mod io;
pub(super) mod snapshot;

pub use backends::{LoggingBackend, NoOpBackend, TelemetryBackend};
pub use collector::MemoryMetricsCollector;
pub use snapshot::MemorySnapshot;

#[cfg(test)]
mod tests {
    use super::*;

    include!("tests.rs");
}
