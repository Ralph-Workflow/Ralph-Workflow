// Parser health statistics.
//
// Contains the ParserHealth struct for tracking event processing statistics.

/// Parser health statistics
#[derive(Debug, Default, Clone, Copy)]
pub struct ParserHealth {
    /// Total number of events processed
    pub total_events: u64,
    /// Number of events successfully parsed and displayed
    pub parsed_events: u64,
    /// Number of partial/delta events (streaming content displayed incrementally)
    pub partial_events: u64,
    /// Number of events ignored (malformed JSON, unknown events, etc.)
    pub ignored_events: u64,
    /// Number of control events (state management, no user output)
    pub control_events: u64,
    /// Number of unknown event types (valid JSON but unhandled)
    pub unknown_events: u64,
    /// Number of JSON parse errors (malformed JSON)
    pub parse_errors: u64,
}

impl ParserHealth {
    /// Create a new health tracker
    #[must_use] 
    pub fn new() -> Self {
        Self::default()
    }

    /// Record a parsed event
    pub const fn record_parsed(&mut self) {
        self.total_events = self.total_events.saturating_add(1);
        self.parsed_events = self.parsed_events.saturating_add(1);
    }

    /// Record an ignored event
    pub const fn record_ignored(&mut self) {
        self.total_events = self.total_events.saturating_add(1);
        self.ignored_events = self.ignored_events.saturating_add(1);
    }

    /// Record an unknown event type (valid JSON but unhandled)
    ///
    /// Unknown events are valid JSON that the parser deserialized successfully
    /// but doesn't have specific handling for. These should not trigger health
    /// warnings as they represent future/new event types, not parser errors.
    pub const fn record_unknown_event(&mut self) {
        self.total_events = self.total_events.saturating_add(1);
        self.unknown_events = self.unknown_events.saturating_add(1);
        self.ignored_events = self.ignored_events.saturating_add(1);
    }

    /// Record a parse error (malformed JSON)
    pub const fn record_parse_error(&mut self) {
        self.total_events = self.total_events.saturating_add(1);
        self.parse_errors = self.parse_errors.saturating_add(1);
        self.ignored_events = self.ignored_events.saturating_add(1);
    }

    /// Record a control event (state management with no user-facing output)
    ///
    /// Control events are valid JSON that represent state transitions
    /// rather than user-facing content. They should not be counted as
    /// "ignored" for health monitoring purposes.
    pub const fn record_control_event(&mut self) {
        self.total_events = self.total_events.saturating_add(1);
        self.control_events = self.control_events.saturating_add(1);
    }

    /// Record a partial/delta event (streaming content displayed incrementally)
    ///
    /// Partial events represent streaming content that is shown to the user
    /// in real-time as deltas. These are NOT errors and should not trigger
    /// health warnings. They are tracked separately to show streaming activity.
    pub const fn record_partial_event(&mut self) {
        self.total_events = self.total_events.saturating_add(1);
        self.partial_events = self.partial_events.saturating_add(1);
    }

    /// Get the percentage of parse errors (excluding unknown events)
    ///
    /// Returns percentage using integer-safe arithmetic to avoid precision loss warnings.
    #[must_use] 
    pub fn parse_error_percentage(&self) -> f64 {
        if self.total_events == 0 {
            return 0.0;
        }
        // Use integer arithmetic: (errors * 10000) / total, then divide by 100.0
        // This gives two decimal places of precision without casting u64 to f64
        let percent_hundredths = self
            .parse_errors
            .saturating_mul(10000)
            .checked_div(self.total_events)
            .unwrap_or(0);
        // Convert to f64 only after scaling down to a reasonable range
        // percent_hundredths is at most 10000 (100% * 100), which fits precisely in f64
        let scaled: u32 = u32::try_from(percent_hundredths)
            .unwrap_or(u32::MAX)
            .min(10000);
        f64::from(scaled) / 100.0
    }

    /// Get the percentage of parse errors as a rounded integer.
    ///
    /// This is for display purposes where a whole number is sufficient.
    #[must_use] 
    pub fn parse_error_percentage_int(&self) -> u32 {
        if self.total_events == 0 {
            return 0;
        }
        // (errors * 100) / total gives us the integer percentage
        self.parse_errors
            .saturating_mul(100)
            .checked_div(self.total_events)
            .and_then(|v| u32::try_from(v).ok())
            .unwrap_or(0)
            .min(100)
    }

    /// Check if the parser health is concerning
    ///
    /// Only returns true if there are actual parse errors (malformed JSON),
    /// not just unknown event types. Unknown events are valid JSON that we
    /// don't have specific handling for, which is not a health concern.
    #[must_use] 
    pub fn is_concerning(&self) -> bool {
        self.total_events > 10 && self.parse_error_percentage() > 50.0
    }

    /// Get a warning message if health is concerning
    #[must_use] 
    pub fn warning(&self, parser_name: &str, colors: Colors) -> Option<String> {
        if !self.is_concerning() {
            return None;
        }

        let msg = if self.unknown_events > 0 || self.control_events > 0 || self.partial_events > 0 {
            format!(
                "{}[Parser Health Warning]{} {} parser has {} parse errors ({}% of {} events). \
                 Also encountered {} unknown event types (valid JSON but unhandled), \
                 {} control events (state management), \
                 and {} partial events (streaming deltas). \
                 This may indicate a parser mismatch. Consider using a different json_parser in your agent config.",
                colors.yellow(),
                colors.reset(),
                parser_name,
                self.parse_errors,
                self.parse_error_percentage_int(),
                self.total_events,
                self.unknown_events,
                self.control_events,
                self.partial_events
            )
        } else {
            format!(
                "{}[Parser Health Warning]{} {} parser has {} parse errors ({}% of {} events). \
                 This may indicate malformed JSON output. Consider using a different json_parser in your agent config.",
                colors.yellow(),
                colors.reset(),
                parser_name,
                self.parse_errors,
                self.parse_error_percentage_int(),
                self.total_events
            )
        };

        Some(msg)
    }
}
