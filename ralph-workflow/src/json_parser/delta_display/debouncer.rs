// Prefix debouncing for streaming display.
//
// Contains StreamingConfig and PrefixDebouncer (test-only).

/// Configuration for streaming display behavior.
///
/// This struct allows customization of streaming output features like
/// prefix debouncing and multi-line handling.
///
/// Default values:
/// - `prefix_delta_threshold`: 0 (disable count-based repetition; first delta still shows prefix)
/// - `prefix_time_threshold`: None (disable time-based repetition)
#[derive(Debug, Clone, Default)]
#[cfg(test)]
pub struct StreamingConfig {
    /// Minimum number of deltas between prefix displays.
    ///
    /// Semantics:
    /// - `0`: disable count-based repetition (prefix is still shown on the first delta)
    /// - `N > 0`: show the prefix every N deltas after the first (i.e., after N suppressed deltas)
    pub prefix_delta_threshold: u32,
    /// Minimum time between prefix displays (None = no time-based debouncing)
    pub prefix_time_threshold: Option<Duration>,
}

/// Controls prefix display frequency during streaming.
///
/// This debouncer reduces visual noise from frequent prefix redisplay during
/// rapid streaming (e.g., character-by-character output). It supports two
/// debouncing strategies:
///
/// 1. **Count-based**: Show prefix every N deltas
/// 2. **Time-based**: Show prefix after M milliseconds since last prefix
///
/// # Example
///
/// ```ignore
/// use std::time::Duration;
///
/// let config = StreamingConfig {
///     prefix_delta_threshold: 5,
///     prefix_time_threshold: Some(Duration::from_millis(100)),
/// };
/// let mut debouncer = PrefixDebouncer::new(config);
///
/// // First delta always shows prefix
/// assert!(debouncer.should_show_prefix(true));
///
/// // Subsequent deltas may skip prefix based on thresholds
/// assert!(!debouncer.should_show_prefix(false)); // Delta 2: skip
/// assert!(!debouncer.should_show_prefix(false)); // Delta 3: skip
/// // ... after threshold reached or time elapsed, prefix shows again
/// ```
#[derive(Debug, Clone)]
#[cfg(test)]
pub struct PrefixDebouncer {
    config: StreamingConfig,
    delta_count: u32,
    last_prefix_time: Option<Instant>,
}

#[cfg(test)]
impl PrefixDebouncer {
    /// Create a new prefix debouncer with the given configuration.
    #[must_use] 
    pub const fn new(config: StreamingConfig) -> Self {
        Self {
            config,
            delta_count: 0,
            last_prefix_time: None,
        }
    }

    /// Reset the debouncer state (e.g., at the start of a new content block).
    pub const fn reset(&mut self) {
        self.delta_count = 0;
        self.last_prefix_time = None;
    }

    /// Determine if the prefix should be shown for the current delta.
    ///
    /// # Arguments
    /// * `is_first_delta` - Whether this is the first delta of a content block
    ///
    /// # Returns
    /// * `true` - Show the prefix
    /// * `false` - Skip the prefix (still perform line clearing)
    pub fn should_show_prefix(&mut self, is_first_delta: bool) -> bool {
        // Always show prefix on first delta
        if is_first_delta {
            self.delta_count = 0;
            self.last_prefix_time = Some(Instant::now());
            return true;
        }

        self.delta_count = self.delta_count.saturating_add(1);

        // Check time-based threshold
        if let Some(threshold) = self.config.prefix_time_threshold {
            if let Some(last_time) = self.last_prefix_time {
                if last_time.elapsed() >= threshold {
                    self.delta_count = 0;
                    self.last_prefix_time = Some(Instant::now());
                    return true;
                }
            }
        }

        // Check count-based threshold
        if self.config.prefix_delta_threshold > 0
            && self.delta_count >= self.config.prefix_delta_threshold
        {
            self.delta_count = 0;
            self.last_prefix_time = Some(Instant::now());
            return true;
        }

        // Default behavior: only first delta shows prefix.
        // With no thresholds configured, subsequent deltas don't show prefix.
        // This preserves the original behavior while allowing opt-in debouncing.
        false
    }
}

#[cfg(test)]
impl Default for PrefixDebouncer {
    fn default() -> Self {
        Self::new(StreamingConfig::default())
    }
}
