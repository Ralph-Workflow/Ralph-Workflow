//! Logger struct and its implementations.
//!
//! This file contains the Logger struct and all its impl blocks,
//! including the Loggable trait implementation.

use crate::checkpoint::timestamp;
use crate::logger::io::stdout_writer::{stderr_write_line, stdout_write_line};
use crate::logger::output::Loggable;
use crate::logger::{
    Colors, ARROW, BOX_BL, BOX_BR, BOX_H, BOX_TL, BOX_TR, BOX_V, CHECK, CROSS, INFO, WARN,
};
use crate::workspace::Workspace;
use std::sync::Arc;

use crate::logger::io::ansi_stripper::strip_ansi_codes;
use crate::logger::io::file_writer::append_to_file;

/// Logger for Ralph output.
///
/// Provides consistent, colorized output with optional file logging.
/// All messages include timestamps and appropriate icons.
pub struct Logger {
    colors: Colors,
    /// Path for direct filesystem logging (CLI layer before workspace available).
    log_file: Option<String>,
    /// Workspace for abstracted file logging (preferred when workspace is available).
    workspace: Option<Arc<dyn Workspace>>,
    /// Relative path within workspace for log file.
    workspace_log_path: Option<String>,
}

impl Logger {
    /// Create a new Logger with the given colors configuration.
    #[must_use]
    pub const fn new(colors: Colors) -> Self {
        Self {
            colors,
            log_file: None,
            workspace: None,
            workspace_log_path: None,
        }
    }

    /// Configure the logger to also write to a file using direct filesystem access.
    ///
    /// Log messages written to the file will have ANSI codes stripped.
    ///
    /// # Note
    ///
    /// For pipeline code where a workspace exists, prefer `with_workspace_log`
    /// instead. This method uses `std::fs` directly and is intended for CLI layer
    /// code or legacy compatibility.
    #[must_use]
    pub fn with_log_file(self, path: &str) -> Self {
        Self {
            colors: self.colors,
            log_file: Some(path.to_string()),
            workspace: self.workspace,
            workspace_log_path: self.workspace_log_path,
        }
    }

    /// Configure the logger to write logs via a workspace.
    ///
    /// This is the preferred method for pipeline code where a workspace exists.
    /// Log messages will be written using the workspace abstraction, allowing
    /// for testing with `MemoryWorkspace`.
    ///
    /// # Arguments
    ///
    /// * `workspace` - The workspace to use for file operations
    /// * `relative_path` - Path relative to workspace root for the log file
    #[must_use]
    pub fn with_workspace_log(self, workspace: Arc<dyn Workspace>, relative_path: &str) -> Self {
        Self {
            colors: self.colors,
            log_file: self.log_file,
            workspace: Some(workspace),
            workspace_log_path: Some(relative_path.to_string()),
        }
    }

    /// Write a message to the log file (if configured).
    fn log_to_file(&self, msg: &str) {
        // Strip ANSI codes for file logging
        let clean_msg = strip_ansi_codes(msg);

        // Try workspace-based logging first
        if let (Some(workspace), Some(ref path)) = (&self.workspace, &self.workspace_log_path) {
            let path = std::path::Path::new(path);
            // Create parent directories if needed
            if let Some(parent) = path.parent() {
                let _ = workspace.create_dir_all(parent);
            }
            // Append to the log file
            let _ = workspace.append_bytes(path, format!("{clean_msg}\n").as_bytes());
            return;
        }

        // Fall back to direct filesystem logging (CLI layer before workspace available)
        if let Some(ref path) = self.log_file {
            let _ = append_to_file(path, &clean_msg);
        }
    }

    /// Log an informational message.
    pub fn info(&self, msg: &str) {
        let c = &self.colors;
        let formatted = format!(
            "{}[{}]{} {}{}{} {}",
            c.dim(),
            timestamp(),
            c.reset(),
            c.blue(),
            INFO,
            c.reset(),
            msg
        );
        let _ = stdout_write_line(&formatted);
        self.log_to_file(&format!("[{}] [INFO] {}", timestamp(), msg));
    }

    /// Log a success message.
    pub fn success(&self, msg: &str) {
        let c = &self.colors;
        let formatted = format!(
            "{}[{}]{} {}{}{} {}{}{}",
            c.dim(),
            timestamp(),
            c.reset(),
            c.green(),
            CHECK,
            c.reset(),
            c.green(),
            msg,
            c.reset()
        );
        let _ = stdout_write_line(&formatted);
        self.log_to_file(&format!("[{}] [OK] {}", timestamp(), msg));
    }

    /// Log a warning message.
    pub fn warn(&self, msg: &str) {
        let c = &self.colors;
        let formatted = format!(
            "{}[{}]{} {}{}{} {}{}{}",
            c.dim(),
            timestamp(),
            c.reset(),
            c.yellow(),
            WARN,
            c.reset(),
            c.yellow(),
            msg,
            c.reset()
        );
        let _ = stdout_write_line(&formatted);
        self.log_to_file(&format!("[{}] [WARN] {}", timestamp(), msg));
    }

    /// Log an error message.
    pub fn error(&self, msg: &str) {
        let c = &self.colors;
        let formatted = format!(
            "{}[{}]{} {}{}{} {}{}{}",
            c.dim(),
            timestamp(),
            c.reset(),
            c.red(),
            CROSS,
            c.reset(),
            c.red(),
            msg,
            c.reset()
        );
        let _ = stderr_write_line(&formatted);
        self.log_to_file(&format!("[{}] [ERROR] {}", timestamp(), msg));
    }

    /// Log a step/action message.
    pub fn step(&self, msg: &str) {
        let c = &self.colors;
        let formatted = format!(
            "{}[{}]{} {}{}{} {}",
            c.dim(),
            timestamp(),
            c.reset(),
            c.magenta(),
            ARROW,
            c.reset(),
            msg
        );
        let _ = stdout_write_line(&formatted);
        self.log_to_file(&format!("[{}] [STEP] {}", timestamp(), msg));
    }

    /// Print a section header with box drawing.
    ///
    /// # Arguments
    ///
    /// * `title` - The header title text
    /// * `color_fn` - Function that returns the color to use
    pub fn header(&self, title: &str, color_fn: fn(Colors) -> &'static str) {
        let c = self.colors;
        let color = color_fn(c);
        let width = 60;
        let title_len = title.chars().count();
        let padding = (width - title_len - 2) / 2;

        let _ = stdout_write_line("");
        let line1 = format!(
            "{}{}{}{}{}{}",
            color,
            c.bold(),
            BOX_TL,
            BOX_H.to_string().repeat(width),
            BOX_TR,
            c.reset()
        );
        let _ = stdout_write_line(&line1);
        let line2 = format!(
            "{}{}{}{}{}{}{}{}{}{}",
            color,
            c.bold(),
            BOX_V,
            " ".repeat(padding),
            c.white(),
            title,
            color,
            " ".repeat(width - padding - title_len),
            BOX_V,
            c.reset()
        );
        let _ = stdout_write_line(&line2);
        let line3 = format!(
            "{}{}{}{}{}{}",
            color,
            c.bold(),
            BOX_BL,
            BOX_H.to_string().repeat(width),
            BOX_BR,
            c.reset()
        );
        let _ = stdout_write_line(&line3);
    }

    /// Print a sub-header (less prominent than header).
    pub fn subheader(&self, title: &str) {
        let c = &self.colors;
        let _ = stdout_write_line("");
        let line1 = format!("{}{}{} {}{}", c.bold(), c.blue(), ARROW, title, c.reset());
        let _ = stdout_write_line(&line1);
        let line2 = format!("{}{}──{}", c.dim(), "─".repeat(title.len()), c.reset());
        let _ = stdout_write_line(&line2);
    }
}

impl Default for Logger {
    fn default() -> Self {
        Self::new(Colors::new())
    }
}

// ===== Loggable Implementation =====

impl Loggable for Logger {
    fn log(&self, msg: &str) {
        self.log_to_file(msg);
    }

    fn info(&self, msg: &str) {
        let c = &self.colors;
        let formatted = format!(
            "{}[{}]{} {}{}{} {}",
            c.dim(),
            timestamp(),
            c.reset(),
            c.blue(),
            INFO,
            c.reset(),
            msg
        );
        let _ = stdout_write_line(&formatted);
        self.log(&format!("[{}] [INFO] {msg}", timestamp()));
    }

    fn success(&self, msg: &str) {
        let c = &self.colors;
        let formatted = format!(
            "{}[{}]{} {}{}{} {}{}{}",
            c.dim(),
            timestamp(),
            c.reset(),
            c.green(),
            CHECK,
            c.reset(),
            c.green(),
            msg,
            c.reset()
        );
        let _ = stdout_write_line(&formatted);
        self.log(&format!("[{}] [OK] {msg}", timestamp()));
    }

    fn warn(&self, msg: &str) {
        let c = &self.colors;
        let formatted = format!(
            "{}[{}]{} {}{}{} {}{}{}",
            c.dim(),
            timestamp(),
            c.reset(),
            c.yellow(),
            WARN,
            c.reset(),
            c.yellow(),
            msg,
            c.reset()
        );
        let _ = stdout_write_line(&formatted);
        self.log(&format!("[{}] [WARN] {msg}", timestamp()));
    }

    fn error(&self, msg: &str) {
        let c = &self.colors;
        let formatted = format!(
            "{}[{}]{} {}{}{} {}{}{}",
            c.dim(),
            timestamp(),
            c.reset(),
            c.red(),
            CROSS,
            c.reset(),
            c.red(),
            msg,
            c.reset()
        );
        let _ = stderr_write_line(&formatted);
        self.log(&format!("[{}] [ERROR] {msg}", timestamp()));
    }

    fn header(&self, title: &str, color_fn: fn(Colors) -> &'static str) {
        let c = self.colors;
        let color = color_fn(c);
        let width = 60;
        let title_len = title.chars().count();
        let padding = (width - title_len - 2) / 2;

        let _ = stdout_write_line("");
        let line1 = format!(
            "{}{}{}{}{}{}",
            color,
            c.bold(),
            BOX_TL,
            BOX_H.to_string().repeat(width),
            BOX_TR,
            c.reset()
        );
        let _ = stdout_write_line(&line1);
        let line2 = format!(
            "{}{}{}{}{}{}{}{}{}{}",
            color,
            c.bold(),
            BOX_V,
            " ".repeat(padding),
            c.white(),
            title,
            color,
            " ".repeat(width - padding - title_len),
            BOX_V,
            c.reset()
        );
        let _ = stdout_write_line(&line2);
        let line3 = format!(
            "{}{}{}{}{}{}",
            color,
            c.bold(),
            BOX_BL,
            BOX_H.to_string().repeat(width),
            BOX_BR,
            c.reset()
        );
        let _ = stdout_write_line(&line3);
    }
}

#[cfg(test)]
mod tests {
    // =========================================================================
    // Workspace-based logger tests
    // =========================================================================

    #[cfg(feature = "test-utils")]
    mod workspace_tests {
        use super::super::*;
        use crate::workspace::MemoryWorkspace;

        #[test]
        fn test_logger_with_workspace_writes_to_file() {
            let workspace = Arc::new(MemoryWorkspace::new_test());
            let mut logger = Logger::new(Colors::new())
                .with_workspace_log(workspace.clone(), ".agent/logs/test.log");

            // Use the Loggable trait to log a message
            Loggable::info(&mut logger, "test message");

            // Verify the message was written to the workspace
            let content = workspace.get_file(".agent/logs/test.log").unwrap();
            assert!(content.contains("test message"));
            assert!(content.contains("[INFO]"));
        }

        #[test]
        fn test_logger_with_workspace_strips_ansi_codes() {
            let workspace = Arc::new(MemoryWorkspace::new_test());
            let logger = Logger::new(Colors::new())
                .with_workspace_log(workspace.clone(), ".agent/logs/test.log");

            // Log via the internal method that includes ANSI codes
            logger.log("[INFO] \x1b[31mcolored\x1b[0m message");

            let content = workspace.get_file(".agent/logs/test.log").unwrap();
            assert!(content.contains("colored message"));
            assert!(!content.contains("\x1b["));
        }

        #[test]
        fn test_logger_with_workspace_creates_parent_dirs() {
            let workspace = Arc::new(MemoryWorkspace::new_test());
            let logger = Logger::new(Colors::new())
                .with_workspace_log(workspace.clone(), ".agent/logs/nested/deep/test.log");

            Loggable::info(&logger, "nested log");

            // Should have created parent directories
            assert!(workspace.exists(std::path::Path::new(".agent/logs/nested/deep")));
            let content = workspace
                .get_file(".agent/logs/nested/deep/test.log")
                .unwrap();
            assert!(content.contains("nested log"));
        }
    }
}
