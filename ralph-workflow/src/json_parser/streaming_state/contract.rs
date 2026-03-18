// Streaming configuration constants and contract types.
//
// This file contains the delta contract validation constants, thresholds,
// and the state enums (`StreamingState`, `ContentBlockState`) that define
// the streaming protocol.

use std::sync::LazyLock;

// Streaming configuration constants

/// Default threshold for detecting snapshot-as-delta violations (in characters).
///
/// Deltas exceeding this size are flagged as potential snapshots. The value of 200
/// characters was chosen because:
/// - Normal deltas are typically < 100 characters (a few tokens)
/// - Snapshots often contain the full accumulated content (200+ chars)
/// - This threshold catches most violations while minimizing false positives
pub(super) const DEFAULT_SNAPSHOT_THRESHOLD: usize = 200;

/// Minimum allowed snapshot threshold (in characters).
///
/// Values below 50 would cause excessive false positives for normal deltas,
/// as even small text chunks (1-2 sentences) can exceed 30 characters.
const MIN_SNAPSHOT_THRESHOLD: usize = 50;

/// Maximum allowed snapshot threshold (in characters).
///
/// Values above 1000 would allow malicious snapshots to pass undetected,
/// potentially causing exponential duplication bugs.
const MAX_SNAPSHOT_THRESHOLD: usize = 1000;

/// Minimum number of consecutive large deltas required to trigger pattern detection warning.
///
/// This threshold prevents false positives from occasional large deltas.
/// Three consecutive large deltas indicate a pattern (not a one-off event).
pub(super) const DEFAULT_PATTERN_DETECTION_MIN_DELTAS: usize = 3;

/// Maximum number of delta sizes to track per content key for pattern detection.
///
/// Tracking recent delta sizes allows us to detect patterns of repeated large
/// content (a sign of snapshot-as-delta bugs). Ten entries provide sufficient
/// history without excessive memory usage.
pub(super) const DEFAULT_MAX_DELTA_HISTORY: usize = 10;

/// Ralph enforces a **delta contract** for all streaming content.
///
/// Every streaming event must contain only the newly generated text (delta),
/// never the full accumulated content (snapshot).
///
/// # Contract Violations
///
/// If a parser emits snapshot-style content when deltas are expected, it will
/// cause exponential duplication bugs. The `StreamingSession` validates that
/// incoming content is delta-sized and logs warnings when violations are detected.
///
/// # Validation Threshold
///
/// Deltas are expected to be small chunks (typically < 100 chars). If a single
/// "delta" exceeds `snapshot_threshold()` characters, it may indicate a snapshot
/// being treated as a delta.
///
/// # Pattern Detection
///
/// In addition to size threshold, we track patterns of repeated large content
/// which may indicate a snapshot-as-delta bug where the same content is being
/// sent repeatedly as if it were incremental.
///
/// # Environment Variables
///
/// The following environment variables can be set to configure streaming behavior:
///
/// - `RALPH_STREAMING_SNAPSHOT_THRESHOLD`: Threshold for detecting snapshot-as-delta
///   violations (default: 200). Deltas exceeding this size trigger warnings.
///
/// Get the snapshot threshold from an injected environment accessor.
///
/// Reads `RALPH_STREAMING_SNAPSHOT_THRESHOLD`.
/// Valid range: 50-1000 characters.
/// Falls back to default of 200 if not set, not parseable, or out of range.
pub(super) fn snapshot_threshold_from_env_fn(get: impl Fn(&str) -> Option<String>) -> usize {
    get("RALPH_STREAMING_SNAPSHOT_THRESHOLD")
        .and_then(|s| s.parse::<usize>().ok())
        .and_then(|v| {
            if (MIN_SNAPSHOT_THRESHOLD..=MAX_SNAPSHOT_THRESHOLD).contains(&v) {
                Some(v)
            } else {
                None
            }
        })
        .unwrap_or(DEFAULT_SNAPSHOT_THRESHOLD)
}

/// Get the snapshot threshold from environment variable or use default.
///
/// Reads `RALPH_STREAMING_SNAPSHOT_THRESHOLD` env var.
/// Valid range: 50-1000 characters.
/// Falls back to default of 200 if not set, not parseable, or out of range.
pub(super) fn snapshot_threshold() -> usize {
    static THRESHOLD: LazyLock<usize> =
        LazyLock::new(|| snapshot_threshold_from_env_fn(|k| std::env::var(k).ok()));
    *THRESHOLD
}

/// Streaming state for the current message lifecycle.
///
/// Tracks whether we're in the middle of streaming content and whether
/// that content has been displayed to the user.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum StreamingState {
    /// No active streaming - idle state
    #[default]
    Idle,
    /// Currently streaming content deltas
    Streaming,
    /// Content has been finalized (after `MessageStop` or equivalent)
    Finalized,
}

/// State tracking for content blocks during streaming.
///
/// Replaces the boolean `in_content_block` with richer state that tracks
/// which block is active and whether output has started for that block.
/// This prevents "glued text" bugs where block boundaries are crossed
/// without proper finalization.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub enum ContentBlockState {
    /// Not currently inside a content block
    #[default]
    NotInBlock,
    /// Inside a content block with tracking for output state
    InBlock {
        /// The block index/identifier
        index: String,
        /// Whether any content has been output for this block
        started_output: bool,
    },
}
