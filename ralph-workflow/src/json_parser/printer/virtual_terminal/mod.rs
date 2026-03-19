// Virtual terminal implementation for simulating real terminal behavior in tests.
//
// This module provides VirtualTerminal, a test utility that accurately simulates
// terminal rendering behavior including cursor movement, ANSI escape sequences,
// line wrapping, and scrolling.
//
// Architecture:
//
// - **State management** (state module) - Terminal buffer, cursor positioning, text writing
// - **ANSI processing** (ansi module) - Parsing and interpreting ANSI escape sequences
// - **Helper functions** (helpers module) - Utilities for ANSI stripping and carriage return handling

#[cfg(any(test, feature = "test-utils"))]
mod ansi;
#[cfg(any(test, feature = "test-utils"))]
mod helpers;
#[cfg(any(test, feature = "test-utils"))]
mod state;

#[cfg(any(test, feature = "test-utils"))]
use helpers::{apply_cr_overwrite_semantics, strip_ansi_sequences};

/// A virtual terminal that simulates real terminal behavior for testing.
///
/// Unlike [`TestPrinter`] which just collects raw output, this accurately simulates
/// how a real terminal renders text, including:
///
/// - **Cursor positioning**: Tracks row and column
/// - **Carriage return (`\r`)**: Moves cursor to column 0 (doesn't erase)
/// - **Newline (`\n`)**: Moves cursor to next row, column 0
/// - **ANSI clear line (`\x1b[2K`)**: Erases entire current line
/// - **ANSI cursor up (`\x1b[1A`)**: Moves cursor up one row
/// - **ANSI cursor down (`\x1b[1B`)**: Moves cursor down one row
/// - **Text overwriting**: Writing after `\r` replaces previous content
/// - **Line wrapping**: Writing past terminal width wraps to next row
/// - **Scrolling**: Writing past terminal height scrolls content up
///
/// This allows tests to verify what the user actually SEES, not just what was written.
///
/// # Terminal Geometry
///
/// The virtual terminal supports fixed geometry (width and height) to simulate
/// real terminal constraints:
///
/// - **Wrapping**: Writing past column `cols` automatically wraps to next row
/// - **Scrolling**: When cursor advances past `rows`, the screen scrolls up
///   (top row is discarded, new blank row added at bottom)
/// - **No reflow**: Resizing is not supported; geometry is fixed at creation
#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug)]
pub struct VirtualTerminal {
    /// The terminal screen buffer - each element is a line (row)
    /// In geometry mode, this is a fixed-size circular buffer (scrolling)
    /// In unbounded mode, this grows indefinitely
    pub(self) screen: RefCell<Vec<String>>,
    /// Current cursor row (0-indexed, relative to screen buffer)
    pub(self) cursor_row: RefCell<usize>,
    /// Current cursor column (0-indexed)
    pub(self) cursor_col: RefCell<usize>,
    /// Whether to simulate terminal mode (affects `is_terminal()`)
    pub(self) simulated_is_terminal: bool,
    /// Raw write history for debugging
    pub(self) write_history: RefCell<Vec<String>>,
    /// Terminal width in columns (None = unbounded, for backward compatibility)
    pub(self) cols: Option<usize>,
    /// Terminal height in rows (None = unbounded, for backward compatibility)
    pub(self) rows: Option<usize>,
}

#[cfg(any(test, feature = "test-utils"))]
impl VirtualTerminal {
    /// Create a new virtual terminal (simulates being a TTY by default).
    /// This creates an unbounded terminal (no wrapping, no scrolling) for backward compatibility.
    #[must_use]
    pub fn new() -> Self {
        Self {
            screen: RefCell::new(vec![String::new()]),
            cursor_row: RefCell::new(0),
            cursor_col: RefCell::new(0),
            simulated_is_terminal: true,
            write_history: RefCell::new(Vec::new()),
            cols: None,
            rows: None,
        }
    }

    /// Create a new virtual terminal with specified terminal simulation.
    /// This creates an unbounded terminal (no wrapping, no scrolling) for backward compatibility.
    #[must_use]
    pub fn new_with_terminal(is_terminal: bool) -> Self {
        Self {
            screen: RefCell::new(vec![String::new()]),
            cursor_row: RefCell::new(0),
            cursor_col: RefCell::new(0),
            simulated_is_terminal: is_terminal,
            write_history: RefCell::new(Vec::new()),
            cols: None,
            rows: None,
        }
    }

    /// Create a new virtual terminal with fixed geometry (width and height).
    ///
    /// This simulates a real terminal with specific dimensions:
    /// - Writing past `cols` wraps to the next row
    /// - Advancing past `rows` scrolls the screen (top row is discarded)
    ///
    /// # Arguments
    /// * `cols` - Terminal width in columns
    /// * `rows` - Terminal height in rows
    ///
    /// # Example
    /// ```ignore
    /// let term = VirtualTerminal::new_with_geometry(80, 24);
    /// // Writing 100 characters will wrap to multiple rows
    /// ```
    #[must_use]
    pub fn new_with_geometry(cols: usize, rows: usize) -> Self {
        Self {
            screen: RefCell::new(vec![String::new()]),
            cursor_row: RefCell::new(0),
            cursor_col: RefCell::new(0),
            simulated_is_terminal: true,
            write_history: RefCell::new(Vec::new()),
            cols: Some(cols),
            rows: Some(rows),
        }
    }

    /// Get the visible output as the user would see it.
    ///
    /// This returns the final rendered state of the terminal, with all
    /// ANSI sequences processed and overwrites applied.
    pub fn get_visible_output(&self) -> String {
        let screen = self.screen.borrow();
        // Join non-empty lines, trimming trailing whitespace from each
        screen
            .iter()
            .map(|line| line.trim_end().to_string())
            .collect::<Vec<_>>()
            .join("\n")
    }

    /// Get visible lines (non-empty lines only).
    pub fn get_visible_lines(&self) -> Vec<String> {
        self.screen
            .borrow()
            .iter()
            .map(|line| line.trim_end().to_string())
            .filter(|line| !line.is_empty())
            .collect()
    }

    /// Count the number of visible lines (non-empty lines).
    ///
    /// This is useful for detecting multi-line waterfall bugs where
    /// streaming output creates multiple visible lines instead of
    /// updating a single line in place.
    pub fn count_visible_lines(&self) -> usize {
        self.get_visible_lines().len()
    }

    /// Get the full screen content including empty lines.
    ///
    /// This is useful for debugging tests to see the exact screen state.
    pub fn get_screen_content(&self) -> Vec<String> {
        self.screen.borrow().clone()
    }

    /// Get all screen lines (alias for `get_screen_content` for clarity).
    ///
    /// Returns the full screen buffer including empty lines.
    /// This allows tests to assert on exact screen state.
    pub fn get_screen_lines(&self) -> Vec<String> {
        self.get_screen_content()
    }

    /// Get the raw write history for debugging.
    pub fn get_write_history(&self) -> Vec<String> {
        self.write_history.borrow().clone()
    }

    /// Get current cursor position as (row, col).
    pub fn cursor_position(&self) -> (usize, usize) {
        (*self.cursor_row.borrow(), *self.cursor_col.borrow())
    }

    /// Clear the terminal.
    pub fn clear(&self) {
        self.screen.borrow_mut().clear();
        self.screen.borrow_mut().push(String::new());
        *self.cursor_row.borrow_mut() = 0;
        *self.cursor_col.borrow_mut() = 0;
        self.write_history.borrow_mut().clear();
    }

    /// Check for duplicate visible lines (useful for detecting rendering bugs).
    pub fn has_duplicate_lines(&self) -> bool {
        let lines = self.get_visible_lines();
        lines.windows(2).any(|w| !w[1].is_empty() && w[1] == w[0])
    }

    /// Count occurrences of a pattern in the visible output.
    pub fn count_visible_pattern(&self, pattern: &str) -> usize {
        self.get_visible_output().matches(pattern).count()
    }

    /// Get visible output as if ANSI escape sequences were ignored/stripped.
    ///
    /// This simulates what the user would see in a CI/log console that strips or ignores
    /// ANSI escape sequences.
    ///
    /// Model:
    /// - ANSI escape sequences are removed
    /// - Newlines (`\n`) are preserved
    /// - Carriage returns (`\r`) are treated as moving to column 0 on the current line,
    ///   overwriting subsequent text on that same line
    ///
    /// Rationale:
    /// Many log consoles strip ANSI, but still interpret `\r` (e.g., progress bars).
    /// If we ignore `\r`, tests can overestimate visible spam or miss overwrite-related bugs.
    ///
    /// NOTE: We intentionally do not attempt to model cursor up/down movement here.
    pub fn get_visible_output_ansi_stripped(&self) -> String {
        let stripped_writes: Vec<String> = self
            .write_history
            .borrow()
            .iter()
            .map(|write| strip_ansi_sequences(write))
            .collect();

        apply_cr_overwrite_semantics(&stripped_writes.join(""))
    }

    /// Count physical rows occupied by content (accounting for wrapping).
    ///
    /// When content exceeds terminal width, it wraps to multiple physical rows.
    /// This method counts how many rows are actually occupied on screen.
    ///
    /// In unbounded mode (no cols/rows set), returns the number of visible lines.
    ///
    /// # Example
    ///
    /// ```ignore
    /// let term = VirtualTerminal::new_with_geometry(40, 24);
    /// write!(term, "[prefix] {}", "A".repeat(100)).unwrap();
    /// assert!(term.count_physical_rows() > 1); // Content wrapped
    /// ```
    pub fn count_physical_rows(&self) -> usize {
        self.get_screen_lines().len()
    }

    /// Verify if cursor-up pattern would leave orphaned content under wrapping.
    ///
    /// This simulates the failure mode: when content wraps to N rows,
    /// "\x1b[1A\x1b[2K" (cursor up 1, clear line) only clears the last row,
    /// leaving N-1 rows visible (orphaned wrapped content).
    ///
    /// Returns true if the given content would wrap and leave orphans.
    ///
    /// NOTE: This helper approximates display width by stripping ANSI escape sequences
    /// and counting Unicode scalar values. It does not model wide Unicode characters
    /// (emoji/CJK) accurately.
    ///
    /// # Arguments
    ///
    /// * `content` - The content to check for wrapping
    ///
    /// # Example
    ///
    /// ```ignore
    /// let term = VirtualTerminal::new_with_geometry(40, 24);
    /// let long_content = "A".repeat(100);
    /// assert!(term.would_cursor_up_leave_orphans(&long_content));
    /// ```
    pub fn would_cursor_up_leave_orphans(&self, content: &str) -> bool {
        self.cols.is_some_and(|cols| {
            // This helper is used by tests to model the cursor-up/clear-line failure mode
            // under wrapping. We intentionally approximate terminal width here:
            // - strip ANSI escape sequences (colors, cursor movement) since they do not
            //   consume terminal columns
            // - count Unicode scalar values as width 1 (this is imperfect for wide CJK/emoji)
            //
            // Tests that rely on this helper should use plain ASCII content.
            let stripped = strip_ansi_sequences(content);

            debug_assert!(
                stripped.is_ascii(),
                "would_cursor_up_leave_orphans is width-approximate; tests should be ASCII-only"
            );

            let content_len = stripped.chars().count();
            let rows_needed = content_len.div_ceil(cols);
            rows_needed > 1 // If content needs >1 row, cursor-up-1 leaves orphans
        })
    }

    /// Get a debug summary of the terminal state for diagnostics.
    ///
    /// Returns a formatted string showing:
    /// - Terminal geometry (cols x rows)
    /// - Cursor position
    /// - Number of visible lines
    /// - Number of physical rows occupied
    /// - Raw write history summary
    ///
    /// Useful for diagnosing streaming issues in tests.
    pub fn debug_summary(&self) -> String {
        let (row, col) = self.cursor_position();
        let geometry = match (self.cols, self.rows) {
            (Some(c), Some(r)) => format!("{c}x{r}"),
            _ => "unbounded".to_string(),
        };

        format!(
            "VirtualTerminal Debug:\n\
             - Geometry: {}\n\
             - Cursor: ({}, {})\n\
             - Visible lines: {}\n\
             - Physical rows: {}\n\
             - Write history entries: {}\n",
            geometry,
            row,
            col,
            self.count_visible_lines(),
            self.count_physical_rows(),
            self.write_history.borrow().len()
        )
    }

    /// Detect if current screen state shows waterfall pattern.
    ///
    /// Waterfall pattern: multiple consecutive lines with same prefix.
    /// This is a symptom of broken cursor-up or carriage-return patterns.
    ///
    /// # Arguments
    ///
    /// * `prefix` - The prefix to check for repetition (e.g., "[ccs/glm]")
    ///
    /// # Returns
    ///
    /// True if the prefix appears on multiple consecutive lines, indicating
    /// a waterfall bug where each delta created a new visible line instead
    /// of updating in-place.
    ///
    /// # Example
    ///
    /// ```ignore
    /// let term = VirtualTerminal::new();
    /// // ... stream content that should update in-place ...
    /// assert!(!term.has_waterfall_pattern("[ccs/glm]"),
    ///     "Should not have waterfall pattern with append-only streaming");
    /// ```
    pub fn has_waterfall_pattern(&self, prefix: &str) -> bool {
        self.get_visible_lines()
            .iter()
            .filter(|line| line.contains(prefix))
            .take(2)
            .count()
            > 1
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl Default for VirtualTerminal {
    fn default() -> Self {
        Self::new()
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl std::io::Write for VirtualTerminal {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let s =
            std::str::from_utf8(buf).map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e))?;

        // Record raw write for debugging
        self.write_history.borrow_mut().push(s.to_string());

        // Process the string through the terminal emulator
        self.process_string(s);

        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        // Virtual terminal doesn't need flushing - content is immediately available
        Ok(())
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl Printable for VirtualTerminal {
    fn is_terminal(&self) -> bool {
        self.simulated_is_terminal
    }
}
