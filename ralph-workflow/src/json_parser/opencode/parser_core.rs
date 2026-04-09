// OpenCode parser core: struct definition and constructor methods.

use std::cell::RefCell;
use std::rc::Rc;

use super::printer::{Printable, SharedPrinter, StdoutPrinter};
use crate::json_parser::types::ToolActivityTracker;
use io::OpenCodeParserState;

/// `OpenCode` event parser
pub struct OpenCodeParser {
    colors: Colors,
    verbosity: Verbosity,
    log_path: Option<std::path::PathBuf>,
    display_name: String,
    pub(crate) state: OpenCodeParserState,
    show_streaming_metrics: bool,
    printer: SharedPrinter,
    /// Tracks active tool executions for idle-timeout suppression. Incremented when a
    /// tool_use event with status "pending" arrives, saturating-decremented on "completed"
    /// or "error", hard-reset to 0 on "step_finish". "running" is a no-op (already counted).
    pub(super) tool_activity_tracker: ToolActivityTracker,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum MonitorEventClassification {
    Parsed,
    Partial,
    Control,
    Unknown,
    ParseError,
    Ignored,
}

const MAX_XML_SEARCH_BYTES: usize = 512 * 1024;
const MAX_XML_BYTES: usize = 128 * 1024;

impl OpenCodeParser {
    pub(crate) fn new(colors: Colors, verbosity: Verbosity) -> Self {
        Self::with_printer(
            colors,
            verbosity,
            Rc::new(RefCell::new(StdoutPrinter::new())),
        )
    }

    pub(crate) fn with_printer(
        colors: Colors,
        verbosity: Verbosity,
        printer: SharedPrinter,
    ) -> Self {
        let verbose_warnings = matches!(verbosity, Verbosity::Debug);

        Self {
            colors,
            verbosity,
            log_path: None,
            display_name: "OpenCode".to_string(),
            state: OpenCodeParserState::new(verbose_warnings),
            show_streaming_metrics: false,
            printer,
            tool_activity_tracker: ToolActivityTracker::new(),
        }
    }

    /// Register a shared counter that the idle-timeout monitor polls to detect active tool
    /// execution. Incremented on "pending" (new call starting), saturating-decremented on
    /// "completed"/"error", hard-reset to 0 on "step_finish". "running" is a no-op.
    pub(crate) fn with_tool_activity_tracker(
        mut self,
        tracker: std::sync::Arc<std::sync::atomic::AtomicU32>,
    ) -> Self {
        self.tool_activity_tracker = ToolActivityTracker::with_tracker(tracker);
        self
    }

    pub(crate) const fn with_show_streaming_metrics(mut self, show: bool) -> Self {
        self.show_streaming_metrics = show;
        self
    }

    pub(crate) fn with_display_name(mut self, display_name: &str) -> Self {
        self.display_name = display_name.to_string();
        self
    }

    pub(crate) fn with_log_file(mut self, path: &str) -> Self {
        self.log_path = Some(std::path::PathBuf::from(path));
        self
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_terminal_mode(self, mode: TerminalMode) -> Self {
        *self.state.terminal_mode.borrow_mut() = mode;
        self
    }

    #[cfg(feature = "test-utils")]
    pub fn with_printer_for_test(
        colors: Colors,
        verbosity: Verbosity,
        printer: SharedPrinter,
    ) -> Self {
        Self::with_printer(colors, verbosity, printer).with_terminal_mode(TerminalMode::Full)
    }

    #[cfg(feature = "test-utils")]
    #[must_use]
    pub fn with_log_file_for_test(mut self, path: &str) -> Self {
        self.log_path = Some(std::path::PathBuf::from(path));
        self
    }

    #[cfg(feature = "test-utils")]
    pub fn parse_stream_for_test<R: std::io::BufRead>(
        &mut self,
        reader: R,
        workspace: &dyn crate::workspace::Workspace,
    ) -> std::io::Result<()> {
        self.parse_stream(reader, workspace)
    }

    #[cfg(feature = "test-utils")]
    pub fn printer(&self) -> SharedPrinter {
        Rc::clone(&self.printer)
    }

    pub(crate) fn with_printer_mut<R>(&mut self, f: impl FnOnce(&mut dyn Printable) -> R) -> R {
        let mut printer_ref = self.printer.borrow_mut();
        f(&mut *printer_ref)
    }

    #[cfg(feature = "test-utils")]
    pub fn streaming_metrics(&self) -> StreamingQualityMetrics {
        self.state
            .streaming_session
            .borrow()
            .get_streaming_quality_metrics()
    }
}
