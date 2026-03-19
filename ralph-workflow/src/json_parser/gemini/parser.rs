use super::printer::StdoutPrinter;
use io::GeminiParserState;

/// Gemini event parser
pub struct GeminiParser {
    colors: Colors,
    verbosity: Verbosity,
    log_path: Option<std::path::PathBuf>,
    display_name: String,
    state: GeminiParserState,
    show_streaming_metrics: bool,
    printer: StdoutPrinter,
}

impl GeminiParser {
    pub(crate) fn new(colors: Colors, verbosity: Verbosity) -> Self {
        Self::with_printer(colors, verbosity, StdoutPrinter::new())
    }

    pub(crate) fn with_printer(
        colors: Colors,
        verbosity: Verbosity,
        printer: StdoutPrinter,
    ) -> Self {
        let verbose_warnings = matches!(verbosity, Verbosity::Debug);

        Self {
            colors,
            verbosity,
            log_path: None,
            display_name: "Gemini".to_string(),
            state: GeminiParserState::new(verbose_warnings),
            show_streaming_metrics: false,
            printer,
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
    pub fn with_terminal_mode(self, mode: crate::json_parser::TerminalMode) -> Self {
        *self.state.terminal_mode.borrow_mut() = mode;
        self
    }

    #[cfg(feature = "test-utils")]
    pub fn with_printer_for_test(
        colors: Colors,
        verbosity: Verbosity,
        printer: StdoutPrinter,
    ) -> Self {
        Self::with_printer(colors, verbosity, printer)
            .with_terminal_mode(crate::json_parser::TerminalMode::Full)
    }

    #[cfg(feature = "test-utils")]
    #[must_use]
    pub fn with_log_file_for_test(mut self, path: &str) -> Self {
        self.log_path = Some(std::path::PathBuf::from(path));
        self
    }

    #[cfg(feature = "test-utils")]
    pub fn parse_stream_for_test<R: std::io::BufRead>(
        &self,
        reader: R,
        workspace: &dyn crate::workspace::Workspace,
    ) -> std::io::Result<()> {
        self.parse_stream(reader, workspace)
    }

    #[cfg(feature = "test-utils")]
    pub fn printer(&self) -> StdoutPrinter {
        self.printer.clone()
    }

    pub(crate) fn with_printer_mut<R>(&mut self, f: impl FnOnce(&mut StdoutPrinter) -> R) -> R {
        f(&mut self.printer)
    }

    #[cfg(feature = "test-utils")]
    pub fn streaming_metrics(&self) -> crate::json_parser::health::StreamingQualityMetrics {
        self.state
            .streaming_session
            .borrow()
            .get_streaming_quality_metrics()
    }
}
