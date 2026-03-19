// Printer tests.

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_stdout_printer() {
        let mut printer = StdoutPrinter::new();
        // Just ensure it compiles and works
        let result = printer.write_all(b"test\n");
        assert!(result.is_ok());
        assert!(printer.flush().is_ok());

        // Verify is_terminal() method is accessible
        let _is_term = printer.is_terminal();
    }

    #[cfg(test)]
    #[test]
    fn test_printable_trait_is_terminal() {
        let printer = StdoutPrinter::new();
        // Test that the Printable trait's is_terminal method works
        let _should_use_colors = printer.is_terminal();
    }

    #[test]
    #[cfg(any(test, feature = "test-utils"))]
    fn test_stderr_printer() {
        let mut printer = StderrPrinter::new();
        // Just ensure it compiles and works
        let result = printer.write_all(b"test\n");
        assert!(result.is_ok());
        assert!(printer.flush().is_ok());
    }

    #[test]
    #[cfg(any(test, feature = "test-utils"))]
    fn test_printer_captures_output() {
        let mut printer = TestPrinter::new();

        printer
            .write_all(b"Hello World\n")
            .expect("Failed to write");
        printer.flush().expect("Failed to flush");

        let output = printer.get_output();
        assert!(output.contains("Hello World"));
    }

    #[test]
    #[cfg(any(test, feature = "test-utils"))]
    fn test_printer_get_lines() {
        let mut printer = TestPrinter::new();

        printer.write_all(b"Line 1\nLine 2\n").unwrap();
        printer.flush().unwrap();

        let lines = printer.get_lines();
        assert_eq!(lines.len(), 2);
        assert!(lines[0].contains("Line 1"));
        assert!(lines[1].contains("Line 2"));
    }

    #[test]
    #[cfg(any(test, feature = "test-utils"))]
    fn test_printer_clear() {
        let mut printer = TestPrinter::new();

        printer.write_all(b"Before\n").unwrap();
        printer.flush().unwrap();

        assert!(!printer.get_output().is_empty());

        printer.clear();
        assert!(printer.get_output().is_empty());
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_printer_has_line() {
        let mut printer = TestPrinter::new();

        printer.write_all(b"Hello World\n").unwrap();
        printer.flush().unwrap();

        assert!(printer.has_line("Hello"));
        assert!(printer.has_line("World"));
        assert!(!printer.has_line("Goodbye"));
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_printer_count_pattern() {
        let mut printer = TestPrinter::new();

        printer.write_all(b"test\nmore test\ntest again\n").unwrap();
        printer.flush().unwrap();

        assert_eq!(printer.count_pattern("test"), 3);
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_printer_detects_duplicates() {
        let mut printer = TestPrinter::new();

        printer.write_all(b"Line 1\nLine 1\nLine 2\n").unwrap();
        printer.flush().unwrap();

        assert!(printer.has_duplicate_consecutive_lines());
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_printer_finds_duplicates() {
        let mut printer = TestPrinter::new();

        printer
            .write_all(b"Line 1\nLine 1\nLine 2\nLine 3\nLine 3\n")
            .unwrap();
        printer.flush().unwrap();

        let duplicates = printer.find_duplicate_consecutive_lines();
        assert_eq!(duplicates.len(), 2);
        assert_eq!(duplicates[0].0, 0); // First duplicate at line 0-1
        assert_eq!(duplicates[0].1, "Line 1\n");
        assert_eq!(duplicates[1].0, 3); // Second duplicate at line 3-4
        assert_eq!(duplicates[1].1, "Line 3\n");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_printer_no_false_positives() {
        let mut printer = TestPrinter::new();

        printer.write_all(b"Line 1\nLine 2\nLine 3\n").unwrap();
        printer.flush().unwrap();

        assert!(!printer.has_duplicate_consecutive_lines());
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_printer_buffer_handling() {
        let mut printer = TestPrinter::new();

        // Write without newline - buffer should hold it
        printer.write_all(b"Partial").unwrap();

        // Without flush, content is in buffer but accessible via get_output/get_lines
        // The TestPrinter stores partial content in buffer which is included in get_output
        assert!(printer.get_output().contains("Partial"));

        // Add newline to complete the line
        printer.write_all(b" content\n").unwrap();
        printer.flush().unwrap();

        // Now should have the complete content
        assert!(printer.has_line("Partial content"));

        // Verify the complete output
        let output = printer.get_output();
        assert!(output.contains("Partial content\n"));
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_printer_get_stats() {
        let mut printer = TestPrinter::new();

        printer.write_all(b"Line 1\nLine 2\n").unwrap();
        printer.flush().unwrap();

        let (line_count, char_count) = printer.get_stats();
        assert_eq!(line_count, 2);
        assert!(char_count > 0);
    }

    #[test]
    fn test_shared_stdout() {
        let printer = shared_stdout();
        // Verify the function creates a valid SharedPrinter
        let _borrowed = printer.borrow();
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_streaming_printer_captures_individual_writes() {
        let mut printer = StreamingTestPrinter::new();

        printer.write_all(b"Hello").unwrap();
        printer.write_all(b" ").unwrap();
        printer.write_all(b"World").unwrap();

        assert_eq!(printer.write_count(), 3);
        assert_eq!(printer.get_full_output(), "Hello World");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_streaming_printer_verify_incremental_writes() {
        let mut printer = StreamingTestPrinter::new();

        printer.write_all(b"A").unwrap();
        printer.write_all(b"B").unwrap();
        printer.write_all(b"C").unwrap();
        printer.write_all(b"D").unwrap();

        assert!(printer.verify_incremental_writes(4).is_ok());
        assert!(printer.verify_incremental_writes(5).is_err());
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_streaming_printer_detects_escape_sequences() {
        let mut printer = StreamingTestPrinter::new();

        printer.write_all(b"Normal text").unwrap();
        assert!(!printer.has_any_escape_sequences());

        printer.clear();
        printer.write_all(b"\x1b[2K\rUpdated").unwrap();
        assert!(printer.has_any_escape_sequences());
        assert!(printer.contains_escape_sequence("\x1b[2K"));
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_streaming_printer_strip_ansi() {
        let input = "\x1b[2K\r\x1b[1mBold\x1b[0m text\x1b[1A";
        let stripped = StreamingTestPrinter::strip_ansi(input);
        assert_eq!(stripped, "\rBold text");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_streaming_printer_content_progression() {
        let mut printer = StreamingTestPrinter::new();

        printer.write_all(b"[agent] Hello\n").unwrap();
        printer
            .write_all(b"\x1b[2K\r[agent] Hello World\n")
            .unwrap();

        let progression = printer.get_content_progression();
        assert!(!progression.is_empty());
        // Later entries should contain more content
        if progression.len() >= 2 {
            assert!(progression[1].len() >= progression[0].len());
        }
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_streaming_printer_terminal_simulation() {
        let printer_non_term = StreamingTestPrinter::new();
        assert!(!printer_non_term.is_terminal());

        let printer_term = StreamingTestPrinter::new_with_terminal(true);
        assert!(printer_term.is_terminal());
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_streaming_printer_get_content_at_write() {
        let mut printer = StreamingTestPrinter::new();

        printer.write_all(b"First").unwrap();
        printer.write_all(b"Second").unwrap();
        printer.write_all(b"Third").unwrap();

        assert_eq!(printer.get_content_at_write(0), Some("First".to_string()));
        assert_eq!(printer.get_content_at_write(1), Some("Second".to_string()));
        assert_eq!(printer.get_content_at_write(2), Some("Third".to_string()));
        assert_eq!(printer.get_content_at_write(3), None);
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_streaming_printer_clear() {
        let mut printer = StreamingTestPrinter::new();

        printer.write_all(b"Some content").unwrap();
        assert_eq!(printer.write_count(), 1);

        printer.clear();
        assert_eq!(printer.write_count(), 0);
        assert!(printer.get_full_output().is_empty());
    }

    // =========================================================================
    // VirtualTerminal Tests
    // =========================================================================

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_simple_text() {
        let mut term = VirtualTerminal::new();
        write!(term, "Hello World").unwrap();
        assert_eq!(term.get_visible_output(), "Hello World");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_newlines() {
        let mut term = VirtualTerminal::new();
        write!(term, "Line 1\nLine 2\nLine 3").unwrap();
        assert_eq!(term.get_visible_output(), "Line 1\nLine 2\nLine 3");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_carriage_return_overwrites() {
        let mut term = VirtualTerminal::new();
        // Write "Hello", then \r moves to start, then "World" overwrites
        write!(term, "Hello\rWorld").unwrap();
        assert_eq!(term.get_visible_output(), "World");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_carriage_return_partial_overwrite() {
        let mut term = VirtualTerminal::new();
        // "Hello World" then \r moves to start, "Hi" overwrites first 2 chars
        write!(term, "Hello World\rHi").unwrap();
        // Result: "Hillo World" (only first 2 chars overwritten)
        assert_eq!(term.get_visible_output(), "Hillo World");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_ansi_clear_line() {
        let mut term = VirtualTerminal::new();
        // Write text, clear line, write new text
        write!(term, "Old text\x1b[2K\rNew text").unwrap();
        assert_eq!(term.get_visible_output(), "New text");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_cursor_up() {
        let mut term = VirtualTerminal::new();
        // Line 1, newline, Line 2, cursor up, overwrite Line 1
        write!(term, "Line 1\nLine 2\x1b[1A\rOverwritten").unwrap();
        let lines = term.get_visible_lines();
        assert_eq!(lines.len(), 2);
        assert_eq!(lines[0], "Overwritten");
        assert_eq!(lines[1], "Line 2");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_cursor_down() {
        let mut term = VirtualTerminal::new();
        // Write on row 0, move down, write on row 1
        write!(term, "Row 0\x1b[1B\rRow 1").unwrap();
        let output = term.get_visible_output();
        assert!(output.contains("Row 0"));
        assert!(output.contains("Row 1"));
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_streaming_simulation() {
        // Simulate the legacy cursor-up streaming pattern (historical):
        // 1. Write "[agent] Hello" + newline + cursor up
        // 2. Clear line + carriage return + write "[agent] Hello World" + newline + cursor up
        // 3. Cursor down at end
        let mut term = VirtualTerminal::new();

        // First delta
        write!(term, "[agent] Hello\n\x1b[1A").unwrap();
        assert_eq!(term.get_visible_lines(), vec!["[agent] Hello"]);

        // Second delta (updates in place)
        write!(term, "\x1b[2K\r[agent] Hello World\n\x1b[1A").unwrap();
        assert_eq!(term.get_visible_lines(), vec!["[agent] Hello World"]);

        // Completion (cursor down)
        writeln!(term, "\x1b[1B").unwrap();
        // Should still show the final content
        assert!(term.get_visible_output().contains("[agent] Hello World"));
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_no_duplicate_lines_in_streaming() {
        let mut term = VirtualTerminal::new();

        // Simulate streaming with in-place updates
        write!(term, "[agent] A\n\x1b[1A").unwrap();
        write!(term, "\x1b[2K\r[agent] AB\n\x1b[1A").unwrap();
        write!(term, "\x1b[2K\r[agent] ABC\n\x1b[1A").unwrap();
        writeln!(term, "\x1b[1B").unwrap();

        // Should NOT have duplicate lines
        assert!(
            !term.has_duplicate_lines(),
            "Virtual terminal should not show duplicate lines after streaming. Got: {:?}",
            term.get_visible_lines()
        );

        // Final content should be the complete message
        assert!(term.get_visible_output().contains("[agent] ABC"));
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_ignores_color_codes() {
        let mut term = VirtualTerminal::new();
        // Write with color codes (SGR sequences)
        write!(term, "\x1b[32mGreen\x1b[0m Normal").unwrap();
        assert_eq!(term.get_visible_output(), "Green Normal");
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_is_terminal() {
        let term_tty = VirtualTerminal::new();
        assert!(term_tty.is_terminal());

        let term_non_tty = VirtualTerminal::new_with_terminal(false);
        assert!(!term_non_tty.is_terminal());
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_cursor_position() {
        let mut term = VirtualTerminal::new();

        assert_eq!(term.cursor_position(), (0, 0));

        write!(term, "Hello").unwrap();
        assert_eq!(term.cursor_position(), (0, 5));

        writeln!(term).unwrap();
        assert_eq!(term.cursor_position(), (1, 0));

        write!(term, "World").unwrap();
        assert_eq!(term.cursor_position(), (1, 5));

        write!(term, "\r").unwrap();
        assert_eq!(term.cursor_position(), (1, 0));
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_count_pattern() {
        let mut term = VirtualTerminal::new();
        write!(term, "Hello World\nHello Again\nGoodbye").unwrap();
        assert_eq!(term.count_visible_pattern("Hello"), 2);
        assert_eq!(term.count_visible_pattern("Goodbye"), 1);
        assert_eq!(term.count_visible_pattern("NotFound"), 0);
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_clear() {
        let mut term = VirtualTerminal::new();
        write!(term, "Some content\nMore content").unwrap();
        assert!(!term.get_visible_output().is_empty());

        term.clear();
        assert!(term.get_visible_output().is_empty());
        assert_eq!(term.cursor_position(), (0, 0));
    }

    #[cfg(any(test, feature = "test-utils"))]
    #[test]
    fn test_virtual_terminal_write_history() {
        let mut term = VirtualTerminal::new();
        write!(term, "First").unwrap();
        write!(term, "Second").unwrap();
        write!(term, "Third").unwrap();

        let history = term.get_write_history();
        assert_eq!(history.len(), 3);
        assert_eq!(history[0], "First");
        assert_eq!(history[1], "Second");
        assert_eq!(history[2], "Third");
    }
}
