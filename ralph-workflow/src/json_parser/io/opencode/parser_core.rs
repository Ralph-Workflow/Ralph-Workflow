// OpenCode parser core: struct definition and constructor methods.

/// `OpenCode` event parser
pub struct OpenCodeParser {
    colors: Colors,
    verbosity: Verbosity,
    /// Relative path to log file (if logging enabled)
    log_path: Option<std::path::PathBuf>,
    display_name: String,
    /// Unified streaming session for state tracking
    streaming_session: Rc<RefCell<StreamingSession>>,
    /// Terminal mode for output formatting
    terminal_mode: RefCell<TerminalMode>,
    /// Track last rendered content for append-only streaming.
    last_rendered_content: RefCell<std::collections::HashMap<String, String>>,
    /// Whether to show streaming quality metrics
    show_streaming_metrics: bool,
    /// Output printer for capturing or displaying output
    printer: SharedPrinter,
    /// Counter for step IDs when events lack stable identifiers
    fallback_step_counter: Cell<u64>,
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
            crate::json_parser::printer::shared_stdout(),
        )
    }

    /// Create a new `OpenCodeParser` with a custom printer.
    ///
    /// # Arguments
    ///
    /// * `colors` - Colors for terminal output
    /// * `verbosity` - Verbosity level for output
    /// * `printer` - Shared printer for output
    ///
    /// # Returns
    ///
    /// A new `OpenCodeParser` instance
    pub(crate) fn with_printer(
        colors: Colors,
        verbosity: Verbosity,
        printer: SharedPrinter,
    ) -> Self {
        let verbose_warnings = matches!(verbosity, Verbosity::Debug);
        let streaming_session = StreamingSession::new().with_verbose_warnings(verbose_warnings);

        // Use the printer's is_terminal method to validate it's connected correctly
        let _printer_is_terminal = printer.borrow().is_terminal();

        Self {
            colors,
            verbosity,
            log_path: None,
            display_name: "OpenCode".to_string(),
            streaming_session: Rc::new(RefCell::new(streaming_session)),
            terminal_mode: RefCell::new(TerminalMode::detect()),
            last_rendered_content: RefCell::new(std::collections::HashMap::new()),
            show_streaming_metrics: false,
            printer,
            fallback_step_counter: Cell::new(0),
        }
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
        *self.terminal_mode.borrow_mut() = mode;
        self
    }

    /// Create a new parser with a test printer.
    ///
    /// This is the primary entry point for integration tests that need
    /// to capture parser output for verification.
    ///
    /// Defaults to `TerminalMode::Full` for testing streaming behavior.
    /// Integration tests that verify streaming output need Full mode to
    /// see per-delta rendering (non-TTY modes suppress deltas and flush at completion).
    #[cfg(feature = "test-utils")]
    pub fn with_printer_for_test(
        colors: Colors,
        verbosity: Verbosity,
        printer: SharedPrinter,
    ) -> Self {
        Self::with_printer(colors, verbosity, printer).with_terminal_mode(TerminalMode::Full)
    }

    /// Set the log file path for testing.
    ///
    /// This allows tests to verify log file content after parsing.
    #[cfg(feature = "test-utils")]
    #[must_use]
    pub fn with_log_file_for_test(mut self, path: &str) -> Self {
        self.log_path = Some(std::path::PathBuf::from(path));
        self
    }

    /// Parse a stream for testing purposes.
    ///
    /// This exposes the internal `parse_stream` method for integration tests.
    ///
    /// # Errors
    ///
    /// Returns an error if stream parsing or file operations fail.
    #[cfg(feature = "test-utils")]
    pub fn parse_stream_for_test<R: std::io::BufRead>(
        &self,
        reader: R,
        workspace: &dyn crate::workspace::Workspace,
    ) -> std::io::Result<()> {
        self.parse_stream(reader, workspace)
    }

    /// Get a shared reference to the printer.
    ///
    /// This allows tests, monitoring, and other code to access the printer after parsing
    /// to verify output content, check for duplicates, or capture output for analysis.
    /// Only available with the `test-utils` feature.
    ///
    /// # Returns
    ///
    /// A clone of the shared printer reference (`Rc<RefCell<dyn Printable>>`)
    #[cfg(feature = "test-utils")]
    pub fn printer(&self) -> SharedPrinter {
        Rc::clone(&self.printer)
    }

    /// Get streaming quality metrics from the current session.
    ///
    /// This provides insight into the deduplication and streaming quality of the
    /// parsing session. Only available with the `test-utils` feature.
    ///
    /// # Returns
    ///
    /// A copy of the streaming quality metrics from the internal `StreamingSession`.
    #[cfg(feature = "test-utils")]
    pub fn streaming_metrics(&self) -> StreamingQualityMetrics {
        self.streaming_session
            .borrow()
            .get_streaming_quality_metrics()
    }
}
