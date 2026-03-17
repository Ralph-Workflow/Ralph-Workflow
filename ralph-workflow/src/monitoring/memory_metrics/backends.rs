// Telemetry backend trait and built-in implementations.

use std::rc::Rc;

use super::snapshot::MemorySnapshot;

/// Pluggable backend for telemetry integration.
///
/// Implement this trait to integrate with external monitoring systems
/// (Prometheus, `DataDog`, `CloudWatch`, etc.)
pub trait TelemetryBackend {
    /// Emit a memory snapshot to the telemetry system.
    fn emit_snapshot(&self, snapshot: &MemorySnapshot);

    /// Emit a warning when memory usage approaches threshold.
    fn emit_warning(&self, message: &str);

    /// Flush any buffered metrics.
    fn flush(&self);
}

/// No-op telemetry backend for testing.
#[derive(Debug, Default)]
pub struct NoOpBackend;

impl TelemetryBackend for NoOpBackend {
    fn emit_snapshot(&self, _snapshot: &MemorySnapshot) {}
    fn emit_warning(&self, _message: &str) {}
    fn flush(&self) {}
}

/// Logging-based telemetry backend.
///
/// Routes metrics through the project's logger implementation.
pub struct LoggingBackend {
    warn_threshold_bytes: usize,
    logger: Rc<dyn crate::logger::Loggable>,
}

impl LoggingBackend {
    /// Create a logging backend that writes via the provided logger.
    pub fn with_logger(
        warn_threshold_bytes: usize,
        logger: Rc<dyn crate::logger::Loggable>,
    ) -> Self {
        Self {
            warn_threshold_bytes,
            logger,
        }
    }
}

impl TelemetryBackend for LoggingBackend {
    fn emit_snapshot(&self, snapshot: &MemorySnapshot) {
        self.logger.info(&format!(
            "[METRICS] iteration={} history_len={} heap_bytes={} checkpoint_count={}",
            snapshot.iteration,
            snapshot.execution_history_len,
            snapshot.execution_history_heap_bytes,
            snapshot.checkpoint_count
        ));

        if snapshot.execution_history_heap_bytes > self.warn_threshold_bytes {
            self.emit_warning(&format!(
                "Execution history heap size {} bytes exceeds warning threshold {} bytes",
                snapshot.execution_history_heap_bytes, self.warn_threshold_bytes
            ));
        }
    }

    fn emit_warning(&self, message: &str) {
        self.logger.warn(&format!("[METRICS WARNING] {message}"));
    }

    fn flush(&self) {
        // Logging backend doesn't buffer
    }
}
