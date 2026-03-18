//! Error event handling for streaming failures.
//!
//! ## Overview
//!
//! Handles `error` events from the streaming API and unknown event types.
//! Error events emit formatted error messages with the agent prefix.
//! Unknown events are logged only in debug mode.

impl crate::json_parser::claude::ClaudeParser {
    /// Handle error events from the streaming API.
    ///
    /// Formats error message with agent prefix and red color in TTY modes.
    pub(in crate::json_parser::io::claude) fn handle_error_event(
        &self,
        err: crate::json_parser::types::StreamError,
    ) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        let msg = err
            .message
            .unwrap_or_else(|| "Unknown streaming error".to_string());
        format!(
            "{}[{}]{} {}Error: {}{}\n",
            c.dim(),
            prefix,
            c.reset(),
            c.red(),
            msg,
            c.reset()
        )
    }

    /// Handle unknown event types.
    ///
    /// In debug mode, logs unknown event with agent prefix.
    /// In production mode, suppresses output to avoid noise.
    pub(in crate::json_parser::io::claude) fn handle_unknown_event(&self) -> String {
        let c = &self.colors;
        let prefix = &self.display_name;

        // Unknown stream event - in debug mode, log it
        if self.verbosity.is_debug() {
            format!(
                "{}[{}]{} {}Unknown streaming event{}\n",
                c.dim(),
                prefix,
                c.reset(),
                c.dim(),
                c.reset()
            )
        } else {
            String::new()
        }
    }
}
