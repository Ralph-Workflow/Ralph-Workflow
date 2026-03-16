//! Logging and progress display utilities.
//!
//! This module provides structured logging for Ralph's pipeline:
//! - `Logger` struct for consistent, colorized output
//! - Progress bar display
//! - Section headers and formatting
//! - Colors & Formatting for terminal output
//! - Test utilities for capturing log output in tests

// The output module is pub so that integration tests can access TestLogger
// when the test-utils feature is enabled.
#[cfg(any(test, feature = "test-utils"))]
pub mod output;
#[cfg(not(any(test, feature = "test-utils")))]
mod output;

mod progress;

pub use output::{argv_requests_json, format_generic_json_for_display, Loggable, Logger};
pub use progress::print_progress;

// ===== Colors & Formatting =====

use std::io::IsTerminal;

/// Environment abstraction for color detection.
///
/// This trait enables testing color detection logic without modifying
/// real environment variables (which would cause test interference).
pub trait ColorEnvironment {
    /// Get an environment variable value.
    fn get_var(&self, name: &str) -> Option<String>;
    /// Check if stdout is a terminal.
    fn is_terminal(&self) -> bool;
}

/// Real environment implementation for production use.
pub struct RealColorEnvironment;

impl ColorEnvironment for RealColorEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn is_terminal(&self) -> bool {
        std::io::stdout().is_terminal()
    }
}

/// Check if colors should be enabled using the provided environment.
///
/// This is the testable version that takes an environment trait.
pub fn colors_enabled_with_env(env: &dyn ColorEnvironment) -> bool {
    // Check NO_COLOR first - this is the strongest user preference
    // See <https://no-color.org/>
    if env.get_var("NO_COLOR").is_some() {
        return false;
    }

    // Check CLICOLOR_FORCE - forces colors even in non-TTY
    // See <https://man.openbsd.org/man1/ls.1#CLICOLOR_FORCE>
    if let Some(val) = env.get_var("CLICOLOR_FORCE") {
        if !val.is_empty() && val != "0" {
            return true;
        }
    }

    // Check CLICOLOR (macOS) - 0 means disable colors
    if let Some(val) = env.get_var("CLICOLOR") {
        if val == "0" {
            return false;
        }
    }

    // Check TERM for dumb terminal
    if let Some(term) = env.get_var("TERM") {
        if term.to_lowercase() == "dumb" {
            return false;
        }
    }

    // Default: color if TTY
    env.is_terminal()
}

/// Check if colors should be enabled.
///
/// This respects standard environment variables for color control:
/// - `NO_COLOR=1`: Disables all ANSI output (<https://no-color.org/>)
/// - `CLICOLOR_FORCE=1`: Forces colors even in non-TTY
/// - `CLICOLOR=0`: Disables colors on macOS
/// - `TERM=dumb`: Disables colors for basic terminals
#[must_use]
pub fn colors_enabled() -> bool {
    colors_enabled_with_env(&RealColorEnvironment)
}

/// ANSI color codes
#[derive(Clone, Copy)]
pub struct Colors {
    pub(crate) enabled: bool,
}

impl Colors {
    #[must_use]
    pub fn new() -> Self {
        Self {
            enabled: colors_enabled(),
        }
    }

    /// **TEST-ONLY:** Create a Colors instance with explicit enabled/disabled state.
    ///
    /// This constructor is for test utilities that need explicit control over
    /// color state, bypassing the automatic detection from `colors_enabled()`.
    ///
    /// # Availability
    ///
    /// **This method is ONLY available in test/test-utils builds** (`#[cfg(any(test, feature = "test-utils"))]`).
    /// It is not part of the public API for library users in production builds.
    /// The extensive documentation here is for maintainers reviewing test code.
    ///
    /// # Example
    ///
    /// ```
    /// # // This doctest only runs when test-utils is enabled
    /// # #[cfg(any(test, feature = "test-utils"))]
    /// # {
    /// use ralph_workflow::logger::Colors;
    ///
    /// // Force colors off for tests checking raw output
    /// let colors = Colors::with_enabled(false);
    /// assert_eq!(colors.bold(), ""); // No ANSI codes
    ///
    /// // Force colors on for tests checking colored output
    /// let colors = Colors::with_enabled(true);
    /// assert_eq!(colors.bold(), "\x1b[1m"); // ANSI bold code
    /// # }
    /// ```
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub const fn with_enabled(enabled: bool) -> Self {
        Self { enabled }
    }

    #[must_use]
    pub const fn bold(self) -> &'static str {
        if self.enabled {
            "\x1b[1m"
        } else {
            ""
        }
    }

    #[must_use]
    pub const fn dim(self) -> &'static str {
        if self.enabled {
            "\x1b[2m"
        } else {
            ""
        }
    }

    #[must_use]
    pub const fn reset(self) -> &'static str {
        if self.enabled {
            "\x1b[0m"
        } else {
            ""
        }
    }

    #[must_use]
    pub const fn red(self) -> &'static str {
        if self.enabled {
            "\x1b[31m"
        } else {
            ""
        }
    }

    #[must_use]
    pub const fn green(self) -> &'static str {
        if self.enabled {
            "\x1b[32m"
        } else {
            ""
        }
    }

    #[must_use]
    pub const fn yellow(self) -> &'static str {
        if self.enabled {
            "\x1b[33m"
        } else {
            ""
        }
    }

    #[must_use]
    pub const fn blue(self) -> &'static str {
        if self.enabled {
            "\x1b[34m"
        } else {
            ""
        }
    }

    #[must_use]
    pub const fn magenta(self) -> &'static str {
        if self.enabled {
            "\x1b[35m"
        } else {
            ""
        }
    }

    #[must_use]
    pub const fn cyan(self) -> &'static str {
        if self.enabled {
            "\x1b[36m"
        } else {
            ""
        }
    }

    #[must_use]
    pub const fn white(self) -> &'static str {
        if self.enabled {
            "\x1b[37m"
        } else {
            ""
        }
    }
}

impl Default for Colors {
    fn default() -> Self {
        Self::new()
    }
}

/// Box-drawing characters for visual structure
pub const BOX_TL: char = '╭';
pub const BOX_TR: char = '╮';
pub const BOX_BL: char = '╰';
pub const BOX_BR: char = '╯';
pub const BOX_H: char = '─';
pub const BOX_V: char = '│';

/// Icons for output
pub const ARROW: char = '→';
pub const CHECK: char = '✓';
pub const CROSS: char = '✗';
pub const WARN: char = '⚠';
pub const INFO: char = 'ℹ';

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    /// Mock environment for testing color detection.
    struct MockColorEnvironment {
        vars: HashMap<String, String>,
        is_tty: bool,
    }

    impl MockColorEnvironment {
        fn new() -> Self {
            Self {
                vars: HashMap::new(),
                is_tty: true,
            }
        }

        fn with_var(mut self, name: &str, value: &str) -> Self {
            self.vars.insert(name.to_string(), value.to_string());
            self
        }

        fn not_tty(mut self) -> Self {
            self.is_tty = false;
            self
        }
    }

    impl ColorEnvironment for MockColorEnvironment {
        fn get_var(&self, name: &str) -> Option<String> {
            self.vars.get(name).cloned()
        }

        fn is_terminal(&self) -> bool {
            self.is_tty
        }
    }

    #[test]
    fn test_colors_disabled_struct() {
        let c = Colors { enabled: false };
        assert_eq!(c.bold(), "");
        assert_eq!(c.red(), "");
        assert_eq!(c.reset(), "");
    }

    #[test]
    fn test_colors_enabled_struct() {
        let c = Colors { enabled: true };
        assert_eq!(c.bold(), "\x1b[1m");
        assert_eq!(c.red(), "\x1b[31m");
        assert_eq!(c.reset(), "\x1b[0m");
    }

    #[test]
    fn test_box_chars() {
        assert_eq!(BOX_TL, '╭');
        assert_eq!(BOX_TR, '╮');
        assert_eq!(BOX_H, '─');
    }

    #[test]
    fn test_colors_enabled_respects_no_color() {
        let env = MockColorEnvironment::new().with_var("NO_COLOR", "1");
        assert!(!colors_enabled_with_env(&env));
    }

    #[test]
    fn test_colors_enabled_respects_clicolor_force() {
        let env = MockColorEnvironment::new()
            .with_var("CLICOLOR_FORCE", "1")
            .not_tty();
        assert!(colors_enabled_with_env(&env));
    }

    #[test]
    fn test_colors_enabled_respects_clicolor_zero() {
        let env = MockColorEnvironment::new().with_var("CLICOLOR", "0");
        assert!(!colors_enabled_with_env(&env));
    }

    #[test]
    fn test_colors_enabled_respects_term_dumb() {
        let env = MockColorEnvironment::new().with_var("TERM", "dumb");
        assert!(!colors_enabled_with_env(&env));
    }

    #[test]
    fn test_colors_enabled_no_color_takes_precedence() {
        let env = MockColorEnvironment::new()
            .with_var("NO_COLOR", "1")
            .with_var("CLICOLOR_FORCE", "1");
        assert!(!colors_enabled_with_env(&env));
    }

    #[test]
    fn test_colors_enabled_term_dumb_case_insensitive() {
        for term in ["dumb", "DUMB", "Dumb", "DuMb"] {
            let env = MockColorEnvironment::new().with_var("TERM", term);
            assert!(
                !colors_enabled_with_env(&env),
                "TERM={term} should disable colors"
            );
        }
    }

    #[test]
    fn test_colors_enabled_default_tty() {
        let env = MockColorEnvironment::new();
        assert!(colors_enabled_with_env(&env));
    }

    #[test]
    fn test_colors_enabled_default_not_tty() {
        let env = MockColorEnvironment::new().not_tty();
        assert!(!colors_enabled_with_env(&env));
    }
}
