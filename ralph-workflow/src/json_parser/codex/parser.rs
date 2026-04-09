use super::printer::{SharedPrinter, StdoutPrinter};
use crate::json_parser::printer::Printable;
use crate::json_parser::types::ToolActivityTracker;
use io::CodexParserState;
use std::cell::RefCell;
use std::rc::Rc;

/// Codex event parser
pub struct CodexParser {
    colors: Colors,
    verbosity: Verbosity,
    log_path: Option<PathBuf>,
    display_name: String,
    state: CodexParserState,
    show_streaming_metrics: bool,
    printer: SharedPrinter,
    /// Tracks active tool executions for idle-timeout suppression. Incremented on
    /// `ItemStarted`, saturating-decremented on `ItemCompleted`, hard-reset to 0 on
    /// `TurnCompleted` / `TurnFailed`.
    tool_activity_tracker: ToolActivityTracker,
}

impl CodexParser {
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
            display_name: "Codex".to_string(),
            state: CodexParserState::new(verbose_warnings),
            show_streaming_metrics: false,
            printer,
            tool_activity_tracker: ToolActivityTracker::new(),
        }
    }

    /// Register a shared counter that the idle-timeout monitor polls to detect active tool
    /// execution. Incremented on `ItemStarted`, saturating-decremented on `ItemCompleted`,
    /// hard-reset to 0 on `TurnCompleted` / `TurnFailed`. This prevents the idle-timeout monitor
    /// from killing the agent during long-running writes or other concurrent tool calls.
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
        self.log_path = Some(PathBuf::from(path));
        self
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_terminal_mode(self, mode: crate::json_parser::TerminalMode) -> Self {
        *self.state.terminal_mode.borrow_mut() = mode;
        self
    }

    #[cfg(any(test, feature = "test-utils"))]
    pub fn with_printer_for_test(
        colors: Colors,
        verbosity: Verbosity,
        printer: SharedPrinter,
    ) -> Self {
        Self::with_printer(colors, verbosity, printer)
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_log_file_for_test(mut self, path: &str) -> Self {
        self.log_path = Some(PathBuf::from(path));
        self
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_display_name_for_test(mut self, display_name: &str) -> Self {
        self.display_name = display_name.to_string();
        self
    }

    #[cfg(any(test, feature = "test-utils"))]
    pub fn parse_stream_for_test<R: std::io::BufRead>(
        &mut self,
        reader: R,
        workspace: &dyn Workspace,
    ) -> std::io::Result<()> {
        self.parse_stream(reader, workspace)
    }

    #[cfg(any(test, feature = "test-utils"))]
    pub fn printer(&self) -> SharedPrinter {
        Rc::clone(&self.printer)
    }

    pub(crate) fn with_printer_mut<R>(&mut self, f: impl FnOnce(&mut dyn Printable) -> R) -> R {
        let mut printer_ref = self.printer.borrow_mut();
        f(&mut *printer_ref)
    }

    #[cfg(any(test, feature = "test-utils"))]
    pub fn streaming_metrics(&self) -> crate::json_parser::health::StreamingQualityMetrics {
        self.state
            .streaming_session
            .borrow()
            .get_streaming_quality_metrics()
    }

    #[inline]
    fn optional_output(output: String) -> Option<String> {
        if output.is_empty() {
            None
        } else {
            Some(output)
        }
    }
}
