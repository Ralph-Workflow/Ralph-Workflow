// Claude parser tests.

// These tests exercise monitoring/test-only APIs; they require the `test-utils` feature.
#[cfg(all(test, feature = "test-utils"))]
mod tests {
    use super::*;
    use crate::json_parser::printer::{SharedPrinter, TestPrinter};

    #[test]
    fn test_printer_field_accessible() {
        // Test that the printer field is accessible and returns a SharedPrinter
        let test_printer: SharedPrinter = Rc::new(RefCell::new(TestPrinter::new()));
        let parser =
            ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, Rc::clone(&test_printer));

        // This test verifies the printer field is accessible
        let _printer_ref = &parser.printer;
    }

    #[test]
    fn test_show_streaming_metrics_builder() {
        // Test that the with_show_streaming_metrics builder method works
        let test_printer: SharedPrinter = Rc::new(RefCell::new(TestPrinter::new()));
        let parser =
            ClaudeParser::with_printer(Colors::new(), Verbosity::Normal, Rc::clone(&test_printer))
                .with_show_streaming_metrics(true);

        // This test verifies the builder method is accessible
        assert!(parser.show_streaming_metrics);
    }
}
