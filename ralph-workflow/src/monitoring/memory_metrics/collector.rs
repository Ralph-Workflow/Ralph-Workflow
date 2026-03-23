// MemoryMetricsCollector: collects and stores memory snapshots during pipeline execution.

use super::backends::TelemetryBackend;
use super::snapshot::MemorySnapshot;

const DEFAULT_MAX_SNAPSHOTS: usize = 1024;

/// Memory metrics collector for pipeline execution.
#[derive(Debug, Clone)]
pub struct MemoryMetricsCollector {
    snapshots: Vec<MemorySnapshot>,
    snapshot_interval: u32,
}

impl MemoryMetricsCollector {
    /// Create a new metrics collector.
    ///
    /// # Arguments
    ///
    /// * `snapshot_interval` - Take snapshot every N iterations (0 = disabled)
    #[must_use]
    pub const fn new(snapshot_interval: u32) -> Self {
        Self {
            snapshots: Vec::new(),
            snapshot_interval,
        }
    }

    fn apply_snapshot_limit(snapshots: Vec<MemorySnapshot>) -> Vec<MemorySnapshot> {
        if snapshots.len() > DEFAULT_MAX_SNAPSHOTS {
            let skip_count = snapshots.len() - DEFAULT_MAX_SNAPSHOTS;
            snapshots.into_iter().skip(skip_count).collect()
        } else {
            snapshots
        }
    }

    /// Record a snapshot if at snapshot interval.
    #[must_use]
    pub fn maybe_record(&self, state: &crate::reducer::PipelineState) -> Self {
        if self.snapshot_interval == 0 {
            return self.clone();
        }

        if state.iteration == 0 {
            return self.clone();
        }

        if state.iteration == 1 || state.iteration.is_multiple_of(self.snapshot_interval) {
            let snapshots = self
                .snapshots
                .clone()
                .into_iter()
                .chain(std::iter::once(MemorySnapshot::from_pipeline_state(state)))
                .collect();
            let snapshots = Self::apply_snapshot_limit(snapshots);
            Self {
                snapshots,
                snapshot_interval: self.snapshot_interval,
            }
        } else {
            self.clone()
        }
    }

    /// Get all recorded snapshots.
    #[must_use]
    pub fn snapshots(&self) -> &[MemorySnapshot] {
        &self.snapshots
    }

    /// Export snapshots as JSON for external analysis.
    ///
    /// # Errors
    ///
    /// Returns error if the operation fails.
    pub fn export_json(&self) -> serde_json::Result<String> {
        serde_json::to_string_pretty(&self.snapshots)
    }

    /// Record a snapshot and send to telemetry backend.
    #[must_use]
    pub fn record_and_emit(
        &self,
        state: &crate::reducer::PipelineState,
        backend: &dyn TelemetryBackend,
    ) -> Self {
        if self.snapshot_interval == 0 {
            return self.clone();
        }

        if state.iteration == 0 {
            return self.clone();
        }

        if state.iteration == 1 || state.iteration.is_multiple_of(self.snapshot_interval) {
            let snapshot = MemorySnapshot::from_pipeline_state(state);
            backend.emit_snapshot(&snapshot);
            let snapshots = self
                .snapshots
                .clone()
                .into_iter()
                .chain(std::iter::once(snapshot))
                .collect();
            let snapshots = Self::apply_snapshot_limit(snapshots);
            Self {
                snapshots,
                snapshot_interval: self.snapshot_interval,
            }
        } else {
            self.clone()
        }
    }
}
