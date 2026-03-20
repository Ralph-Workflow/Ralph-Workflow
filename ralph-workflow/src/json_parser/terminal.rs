//! Terminal mode detection for streaming output.
//!
//! This module provides terminal capability detection to control whether
//! ANSI escape sequences (cursor positioning, colors) should be emitted.

use crate::logger::ColorEnvironment;

/// Terminal capability mode for streaming output.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TerminalMode {
    /// Full ANSI support: cursor positioning, colors, all escapes
    Full,
    /// Basic TTY: colors without cursor positioning
    Basic,
    /// Non-TTY output: no ANSI sequences
    None,
}

impl TerminalMode {
    /// Detect the current terminal mode using the provided environment.
    pub fn detect_with_env(env: &dyn ColorEnvironment) -> Self {
        // Check NO_COLOR first
        if env.get_var("NO_COLOR").is_some() {
            return Self::None;
        }

        // Check CLICOLOR_FORCE
        if let Some(val) = env.get_var("CLICOLOR_FORCE") {
            if val != "0" {
                return if env.is_terminal() {
                    Self::Full
                } else {
                    Self::Basic
                };
            }
        }

        // Check CLICOLOR
        if let Some(val) = env.get_var("CLICOLOR") {
            if val == "0" {
                return Self::None;
            }
        }

        // Check if stdout is a terminal
        if !env.is_terminal() {
            return Self::None;
        }

        // Check TERM for capability detection
        match env.get_var("TERM") {
            Some(term) => {
                let term_lower = term.to_lowercase();

                if term_lower == "dumb" {
                    return Self::Basic;
                }

                let capable_terminals = [
                    "xterm",
                    "xterm-",
                    "vt100",
                    "vt102",
                    "vt220",
                    "vt320",
                    "screen",
                    "tmux",
                    "ansi",
                    "rxvt",
                    "konsole",
                    "gnome-terminal",
                    "iterm",
                    "alacritty",
                    "kitty",
                    "wezterm",
                    "foot",
                ];

                if capable_terminals
                    .iter()
                    .any(|capable| term_lower.starts_with(capable))
                {
                    return Self::Full;
                }

                Self::Basic
            }
            None => Self::Basic,
        }
    }

    /// Detect the current terminal mode from environment.
    #[must_use]
    pub fn detect() -> Self {
        Self::detect_with_env(&RealTerminalEnvironment)
    }
}

impl Default for TerminalMode {
    fn default() -> Self {
        Self::detect()
    }
}

/// Real environment for terminal detection.
struct RealTerminalEnvironment;

impl ColorEnvironment for RealTerminalEnvironment {
    fn get_var(&self, name: &str) -> Option<String> {
        std::env::var(name).ok()
    }

    fn is_terminal(&self) -> bool {
        std::io::IsTerminal::is_terminal(&std::io::stdout())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    struct MockTerminalEnv {
        vars: HashMap<String, String>,
        is_tty: bool,
    }

    impl MockTerminalEnv {
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

    impl ColorEnvironment for MockTerminalEnv {
        fn get_var(&self, name: &str) -> Option<String> {
            self.vars.get(name).cloned()
        }

        fn is_terminal(&self) -> bool {
            self.is_tty
        }
    }

    #[test]
    fn test_terminal_mode_no_color() {
        let env = MockTerminalEnv::new().with_var("NO_COLOR", "1");
        assert_eq!(TerminalMode::detect_with_env(&env), TerminalMode::None);
    }

    #[test]
    fn test_terminal_mode_clicolor_force_tty() {
        let env = MockTerminalEnv::new().with_var("CLICOLOR_FORCE", "1");
        assert_eq!(TerminalMode::detect_with_env(&env), TerminalMode::Full);
    }

    #[test]
    fn test_terminal_mode_clicolor_force_not_tty() {
        let env = MockTerminalEnv::new()
            .with_var("CLICOLOR_FORCE", "1")
            .not_tty();
        assert_eq!(TerminalMode::detect_with_env(&env), TerminalMode::Basic);
    }

    #[test]
    fn test_terminal_mode_clicolor_zero() {
        let env = MockTerminalEnv::new().with_var("CLICOLOR", "0");
        assert_eq!(TerminalMode::detect_with_env(&env), TerminalMode::None);
    }

    #[test]
    fn test_terminal_mode_term_dumb() {
        let env = MockTerminalEnv::new().with_var("TERM", "dumb");
        assert_eq!(TerminalMode::detect_with_env(&env), TerminalMode::Basic);
    }

    #[test]
    fn test_terminal_mode_term_xterm() {
        let env = MockTerminalEnv::new().with_var("TERM", "xterm-256color");
        assert_eq!(TerminalMode::detect_with_env(&env), TerminalMode::Full);
    }

    #[test]
    fn test_terminal_mode_not_tty() {
        let env = MockTerminalEnv::new().not_tty();
        assert_eq!(TerminalMode::detect_with_env(&env), TerminalMode::None);
    }

    #[test]
    fn test_terminal_mode_unknown_term() {
        let env = MockTerminalEnv::new().with_var("TERM", "unknown-terminal");
        assert_eq!(TerminalMode::detect_with_env(&env), TerminalMode::Basic);
    }

    #[test]
    fn test_terminal_mode_partial_eq() {
        assert_eq!(TerminalMode::Full, TerminalMode::Full);
        assert_eq!(TerminalMode::Basic, TerminalMode::Basic);
        assert_eq!(TerminalMode::None, TerminalMode::None);
        assert_ne!(TerminalMode::Full, TerminalMode::Basic);
    }
}
