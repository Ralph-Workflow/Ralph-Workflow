/// Codex event parser
pub struct CodexParser {
    colors: Colors,
    verbosity: Verbosity,
    /// Relative path to log file (if logging enabled)
    log_path: Option<PathBuf>,
    display_name: String,
    /// Unified streaming session for state tracking
    streaming_session: Rc<RefCell<StreamingSession>>,
    /// Delta accumulator for reasoning content (which uses special display)
    /// Note: We keep this for reasoning only, as it uses `DeltaDisplayFormatter`
    reasoning_accumulator: Rc<RefCell<crate::json_parser::types::DeltaAccumulator>>,
    /// Turn counter for generating synthetic turn IDs
    turn_counter: Rc<RefCell<u64>>,
    /// Terminal mode for output formatting
    terminal_mode: RefCell<TerminalMode>,
    /// Whether to show streaming quality metrics
    show_streaming_metrics: bool,
    /// Output printer for capturing or displaying output
    printer: SharedPrinter,
    /// Track last rendered content for append-only streaming pattern.
    ///
    /// Maps content key (e.g., "`text:agent_msg`", "thinking:reasoning") to the
    /// last rendered sanitized content. Used to compute the new suffix for each
    /// delta and emit only the incremental change, avoiding cursor movement and
    /// wrapping issues.
    last_rendered_content: RefCell<std::collections::HashMap<String, String>>,
}

impl CodexParser {
    pub(crate) fn new(colors: Colors, verbosity: Verbosity) -> Self {
        Self::with_printer(
            colors,
            verbosity,
            crate::json_parser::printer::shared_stdout(),
        )
    }

    /// Create a new `CodexParser` with a custom printer.
    pub(crate) fn with_printer(
        colors: Colors,
        verbosity: Verbosity,
        printer: SharedPrinter,
    ) -> Self {
        let verbose_warnings = matches!(verbosity, Verbosity::Debug);
        let streaming_session = StreamingSession::new().with_verbose_warnings(verbose_warnings);

        let _printer_is_terminal = printer.borrow().is_terminal();

        Self {
            colors,
            verbosity,
            log_path: None,
            display_name: "Codex".to_string(),
            streaming_session: Rc::new(RefCell::new(streaming_session)),
            reasoning_accumulator: Rc::new(RefCell::new(
                crate::json_parser::types::DeltaAccumulator::new(),
            )),
            turn_counter: Rc::new(RefCell::new(0)),
            terminal_mode: RefCell::new(TerminalMode::detect()),
            show_streaming_metrics: false,
            printer,
            last_rendered_content: RefCell::new(std::collections::HashMap::new()),
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

    /// Configure log file path.
    ///
    /// The workspace is passed to `parse_stream` separately.
    pub(crate) fn with_log_file(mut self, path: &str) -> Self {
        self.log_path = Some(PathBuf::from(path));
        self
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_terminal_mode(self, mode: TerminalMode) -> Self {
        *self.terminal_mode.borrow_mut() = mode;
        self
    }

    // ===== Test utilities (available with test-utils feature) =====

    /// Create a new parser with a custom printer (for testing).
    ///
    /// This method is public when the `test-utils` feature is enabled,
    /// allowing integration tests (in this repository) to create parsers with custom printers.
    ///
    /// Note: downstream crates should avoid relying on this API in production builds.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn with_printer_for_test(
        colors: Colors,
        verbosity: Verbosity,
        printer: SharedPrinter,
    ) -> Self {
        Self::with_printer(colors, verbosity, printer)
    }

    /// Set the log file path (for testing).
    ///
    /// This method is public when the `test-utils` feature is enabled,
    /// allowing integration tests to configure log file path.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_log_file_for_test(mut self, path: &str) -> Self {
        self.log_path = Some(PathBuf::from(path));
        self
    }

    /// Set the display name (for testing).
    ///
    /// This method is public when the `test-utils` feature is enabled,
    /// allowing integration tests to configure display name.
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_display_name_for_test(mut self, display_name: &str) -> Self {
        self.display_name = display_name.to_string();
        self
    }

    /// Parse a stream of JSON events (for testing).
    ///
    /// This method is public when the `test-utils` feature is enabled,
    /// allowing integration tests to invoke parsing.
    ///
    /// # Errors
    ///
    /// Returns an error if stream parsing or file operations fail.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn parse_stream_for_test<R: std::io::BufRead>(
        &self,
        reader: R,
        workspace: &dyn Workspace,
    ) -> std::io::Result<()> {
        self.parse_stream(reader, workspace)
    }

    /// Get a shared reference to the printer.
    ///
    /// This allows tests, monitoring, and other code to access the printer after parsing
    /// to verify output content, check for duplicates, or capture output for analysis.
    /// Only available with the `test-utils` feature.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn printer(&self) -> SharedPrinter {
        Rc::clone(&self.printer)
    }

    /// Get streaming quality metrics from the current session.
    ///
    /// This provides insight into the deduplication and streaming quality of the
    /// parsing session. Only available with the `test-utils` feature.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn streaming_metrics(&self) -> StreamingQualityMetrics {
        self.streaming_session
            .borrow()
            .get_streaming_quality_metrics()
    }

    /// Convert output string to Option, returning None if empty.
    #[inline]
    fn optional_output(output: String) -> Option<String> {
        if output.is_empty() {
            None
        } else {
            Some(output)
        }
    }
}
