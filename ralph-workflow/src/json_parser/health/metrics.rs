// Streaming quality metrics.
//
// Contains StreamingQualityMetrics and StreamingPattern.

use crate::logger::Colors;

fn classify_streaming_pattern(
    sizes_vec: &[usize],
    total_deltas: usize,
    avg_delta_size: usize,
) -> StreamingPattern {
    if total_deltas < 2 {
        return StreamingPattern::Normal;
    }
    let mean_u32 = u32::try_from(avg_delta_size).unwrap_or(u32::MAX);
    let mean = f64::from(mean_u32);
    if mean < 0.001 {
        return StreamingPattern::Empty;
    }
    classify_by_coefficient_of_variation(sizes_vec, total_deltas, avg_delta_size, mean)
}

fn compute_cv(sizes_vec: &[usize], total_deltas: usize, avg_delta_size: usize, mean: f64) -> f64 {
    let variance_sum: usize = sizes_vec
        .iter()
        .map(|&size| {
            let diff = size.abs_diff(avg_delta_size);
            diff.saturating_mul(diff)
        })
        .sum();
    let variance = variance_sum / total_deltas;
    let variance_u32 = u32::try_from(variance).unwrap_or(u32::MAX);
    f64::from(variance_u32).sqrt() / mean
}

fn cv_to_pattern(cv: f64) -> StreamingPattern {
    if cv < 0.3 {
        StreamingPattern::Smooth
    } else if cv < 1.0 {
        StreamingPattern::Normal
    } else {
        StreamingPattern::Bursty
    }
}

fn classify_by_coefficient_of_variation(
    sizes_vec: &[usize],
    total_deltas: usize,
    avg_delta_size: usize,
    mean: f64,
) -> StreamingPattern {
    cv_to_pattern(compute_cv(sizes_vec, total_deltas, avg_delta_size, mean))
}

fn format_streaming_base(m: &StreamingQualityMetrics, colors: Colors) -> String {
    let pattern_str = match m.pattern {
        StreamingPattern::Empty => "empty",
        StreamingPattern::Smooth => "smooth",
        StreamingPattern::Normal => "normal",
        StreamingPattern::Bursty => "bursty",
    };
    format!(
        "{}[Streaming]{} {} deltas, avg {} bytes (min {}, max {}), pattern: {}",
        colors.dim(),
        colors.reset(),
        m.total_deltas,
        m.avg_delta_size,
        m.min_delta_size,
        m.max_delta_size,
        pattern_str
    )
}

fn push_if_nonzero(parts: &mut Vec<String>, count: usize, label: &str, color: &str, reset: &str) {
    if count > 0 {
        parts.push(format!("{}{}: {}{}", color, label, count, reset));
    }
}

fn collect_streaming_extras(m: &StreamingQualityMetrics, colors: Colors) -> Vec<String> {
    let mut parts = Vec::new();
    push_if_nonzero(
        &mut parts,
        m.snapshot_repairs_count,
        "snapshot repairs",
        colors.yellow(),
        colors.reset(),
    );
    push_if_nonzero(
        &mut parts,
        m.large_delta_count,
        "large deltas",
        colors.yellow(),
        colors.reset(),
    );
    push_if_nonzero(
        &mut parts,
        m.protocol_violations,
        "protocol violations",
        colors.red(),
        colors.reset(),
    );
    if let Some(queue_str) = format_queue_metrics(m, colors) {
        parts.push(queue_str);
    }
    parts
}

fn collect_queue_parts(m: &StreamingQualityMetrics, colors: Colors) -> Vec<String> {
    let mut queue_parts = Vec::new();
    if m.queue_depth > 0 {
        queue_parts.push(format!("depth: {}", m.queue_depth));
    }
    push_if_nonzero(
        &mut queue_parts,
        m.queue_dropped_events,
        "dropped",
        colors.yellow(),
        colors.reset(),
    );
    push_if_nonzero(
        &mut queue_parts,
        m.queue_backpressure_count,
        "backpressure",
        colors.yellow(),
        colors.reset(),
    );
    queue_parts
}

fn format_queue_metrics(m: &StreamingQualityMetrics, colors: Colors) -> Option<String> {
    if m.queue_depth == 0 && m.queue_dropped_events == 0 && m.queue_backpressure_count == 0 {
        return None;
    }
    let queue_parts = collect_queue_parts(m, colors);
    if queue_parts.is_empty() {
        None
    } else {
        Some(format!("queue: {}", queue_parts.join(", ")))
    }
}

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
        let pattern = classify_streaming_pattern(&sizes_vec, total_deltas, avg_delta_size);
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
        let base = format_streaming_base(self, colors);
        let extras = collect_streaming_extras(self, colors);
        if extras.is_empty() {
            base
        } else {
            format!("{base}, {}", extras.join(", "))
        }
    }
}
