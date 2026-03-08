// MemoryMetricsCollector: collects and stores memory snapshots during pipeline execution.

use super::backends::TelemetryBackend;
use super::snapshot::MemorySnapshot;

const DEFAULT_MAX_SNAPSHOTS: usize = 1024;

/// Memory metrics collector for pipeline execution.
#[derive(Debug)]
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

    fn enforce_snapshot_limit(&mut self) {
        if self.snapshots.len() > DEFAULT_MAX_SNAPSHOTS {
            let excess = self.snapshots.len() - DEFAULT_MAX_SNAPSHOTS;
            self.snapshots.drain(0..excess);
        }
    }

    /// Record a snapshot if at snapshot interval.
    pub fn maybe_record(&mut self, state: &crate::reducer::PipelineState) {
        if self.snapshot_interval == 0 {
            return;
        }

        // Treat iteration 0 as "pre-run" (initial state). Recording here is surprising
        // and skews exported metrics since 0 is a multiple of any non-zero interval.
        if state.iteration == 0 {
            return;
        }

        if state.iteration == 1 || state.iteration.is_multiple_of(self.snapshot_interval) {
            self.snapshots
                .push(MemorySnapshot::from_pipeline_state(state));
            self.enforce_snapshot_limit();
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
    pub fn record_and_emit(
        &mut self,
        state: &crate::reducer::PipelineState,
        backend: &mut dyn TelemetryBackend,
    ) {
        if self.snapshot_interval == 0 {
            return;
        }

        if state.iteration == 0 {
            return;
        }

        if state.iteration == 1 || state.iteration.is_multiple_of(self.snapshot_interval) {
            let snapshot = MemorySnapshot::from_pipeline_state(state);
            backend.emit_snapshot(&snapshot);
            self.snapshots.push(snapshot);
            self.enforce_snapshot_limit();
        }
    }
}
