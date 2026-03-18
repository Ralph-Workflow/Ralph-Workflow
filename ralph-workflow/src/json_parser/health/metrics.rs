// Streaming quality metrics.
//
// Contains StreamingQualityMetrics and StreamingPattern.

/// Streaming quality metrics for analyzing streaming behavior.
///
/// These metrics help diagnose issues with streaming performance and
/// inform future improvements to the streaming infrastructure.
///
/// # Metrics Tracked
///
/// - **Delta sizes**: Average, min, max sizes to understand streaming granularity
/// - **Total deltas**: Count of deltas processed
/// - **Streaming pattern**: Classification based on size variance
/// - **Queue metrics**: Event queue depth, dropped events, and backpressure (when using bounded queue)
#[derive(Debug, Clone, Default)]
pub struct StreamingQualityMetrics {
    /// Total number of deltas processed
    pub total_deltas: usize,
    /// Average delta size in bytes
    pub avg_delta_size: usize,
    /// Minimum delta size in bytes
    pub min_delta_size: usize,
    /// Maximum delta size in bytes
    pub max_delta_size: usize,
    /// Classification of streaming pattern
    pub pattern: StreamingPattern,
    /// Number of times auto-repair was triggered for snapshot-as-delta bugs
    pub snapshot_repairs_count: usize,
    /// Number of deltas that exceeded the size threshold (indicating potential snapshots)
    pub large_delta_count: usize,
    /// Number of protocol violations detected (e.g., `MessageStart` during streaming)
    pub protocol_violations: usize,
    /// Queue depth (number of events in queue) - 0 if queue not in use
    pub queue_depth: usize,
    /// Number of events dropped due to queue overflow - 0 if queue not in use
    pub queue_dropped_events: usize,
    /// Number of times backpressure was triggered (send blocked on full queue) - 0 if queue not in use
    pub queue_backpressure_count: usize,
}

/// Classification of streaming patterns based on delta size variance.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StreamingPattern {
    /// No deltas to classify
    #[default]
    Empty,
    /// Uniform delta sizes (low variance) - smooth streaming
    Smooth,
    /// Mixed delta sizes (medium variance) - normal streaming
    Normal,
    /// Highly variable delta sizes (high variance) - bursty/chunked streaming
    Bursty,
}

impl StreamingQualityMetrics {
    /// Create metrics from a collection of delta sizes.
    ///
    /// # Arguments
    /// * `sizes` - Iterator of delta sizes in bytes
    pub fn from_sizes<I: Iterator<Item = usize>>(sizes: I) -> Self {
        let sizes_vec: Vec<_> = sizes.collect();

        if sizes_vec.is_empty() {
            return Self::default();
        }

        let total_deltas = sizes_vec.len();
        let min_delta_size = sizes_vec.iter().copied().min().unwrap_or(0);
        let max_delta_size = sizes_vec.iter().copied().max().unwrap_or(0);
        let sum: usize = sizes_vec.iter().sum();
        let avg_delta_size = sum / total_deltas;

        // Calculate variance to determine pattern
        // Use coefficient of variation: std_dev / mean
        let pattern = if total_deltas < 2 {
            StreamingPattern::Normal
        } else {
            // Convert to u32 for safe f64 conversion (delta sizes are typically small)
            let mean_u32 = u32::try_from(avg_delta_size).unwrap_or(u32::MAX);
            let mean = f64::from(mean_u32);
            if mean < 0.001 {
                StreamingPattern::Empty
            } else {
                // Calculate variance using integer-safe arithmetic
                let variance_sum: usize = sizes_vec
                    .iter()
                    .map(|&size| {
                        let diff = size.abs_diff(avg_delta_size);
                        diff.saturating_mul(diff)
                    })
                    .sum();
                let variance = variance_sum / total_deltas;
                // Convert to u32 for safe f64 conversion
                let variance_u32 = u32::try_from(variance).unwrap_or(u32::MAX);
                let std_dev = f64::from(variance_u32).sqrt();
                let cv = std_dev / mean;

                // Thresholds based on coefficient of variation
                if cv < 0.3 {
                    StreamingPattern::Smooth
                } else if cv < 1.0 {
                    StreamingPattern::Normal
                } else {
                    StreamingPattern::Bursty
                }
            }
        };

        Self {
            total_deltas,
            avg_delta_size,
            min_delta_size,
            max_delta_size,
            pattern,
            snapshot_repairs_count: 0,
            large_delta_count: 0,
            protocol_violations: 0,
            queue_depth: 0,
            queue_dropped_events: 0,
            queue_backpressure_count: 0,
        }
    }

    /// Format metrics for display.
    #[must_use]
    pub fn format(&self, colors: Colors) -> String {
        if self.total_deltas == 0 {
            return format!(
                "{}[Streaming]{} No deltas recorded",
                colors.dim(),
                colors.reset()
            );
        }

        let pattern_str = match self.pattern {
            StreamingPattern::Empty => "empty",
            StreamingPattern::Smooth => "smooth",
            StreamingPattern::Normal => "normal",
            StreamingPattern::Bursty => "bursty",
        };

        let base = format!(
            "{}[Streaming]{} {} deltas, avg {} bytes (min {}, max {}), pattern: {}",
            colors.dim(),
            colors.reset(),
            self.total_deltas,
            self.avg_delta_size,
            self.min_delta_size,
            self.max_delta_size,
            pattern_str
        );

        let parts: Vec<String> = std::iter::once(base)
            .chain(if self.snapshot_repairs_count > 0 {
                Some(format!(
                    "{}snapshot repairs: {}{}",
                    colors.yellow(),
                    self.snapshot_repairs_count,
                    colors.reset()
                ))
            } else {
                None
            })
            .chain(if self.large_delta_count > 0 {
                Some(format!(
                    "{}large deltas: {}{}",
                    colors.yellow(),
                    self.large_delta_count,
                    colors.reset()
                ))
            } else {
                None
            })
            .chain(if self.protocol_violations > 0 {
                Some(format!(
                    "{}protocol violations: {}{}",
                    colors.red(),
                    self.protocol_violations,
                    colors.reset()
                ))
            } else {
                None
            })
            .chain(
                if self.queue_depth > 0
                    || self.queue_dropped_events > 0
                    || self.queue_backpressure_count > 0
                {
                    let queue_parts: Vec<String> = std::iter::empty()
                        .chain(if self.queue_depth > 0 {
                            Some(format!("depth: {}", self.queue_depth))
                        } else {
                            None
                        })
                        .chain(if self.queue_dropped_events > 0 {
                            Some(format!(
                                "{}dropped: {}{}",
                                colors.yellow(),
                                self.queue_dropped_events,
                                colors.reset()
                            ))
                        } else {
                            None
                        })
                        .chain(if self.queue_backpressure_count > 0 {
                            Some(format!(
                                "{}backpressure: {}{}",
                                colors.yellow(),
                                self.queue_backpressure_count,
                                colors.reset()
                            ))
                        } else {
                            None
                        })
                        .collect();
                    if queue_parts.is_empty() {
                        None
                    } else {
                        Some(format!("queue: {}", queue_parts.join(", ")))
                    }
                } else {
                    None
                },
            )
            .collect();

        parts.join(", ")
    }
}
