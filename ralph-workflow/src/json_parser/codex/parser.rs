use super::printer::{SharedPrinter, StdoutPrinter};
use crate::json_parser::printer::Printable;
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
        printer: StdoutPrinter,
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
    pub fn printer(&self) -> StdoutPrinter {
        self.printer.clone()
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
