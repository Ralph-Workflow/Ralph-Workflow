//! Terminal state management for `VirtualTerminal`.
//!
//! This module handles the terminal screen buffer, cursor positioning,
//! and character/string writing operations.

use super::VirtualTerminal;

#[cfg(any(test, feature = "test-utils"))]
impl VirtualTerminal {
    /// Ensure the current row exists in the buffer.
    ///
    /// In geometry mode, this may trigger scrolling if cursor is past the bottom.
    /// In unbounded mode, this grows the buffer as needed.
    pub(super) fn ensure_row_exists(&self) {
        let row = *self.cursor_row.borrow();
        let screen = self.screen.borrow_mut();

        // In geometry mode, check if we need to scroll
        if let Some(max_rows) = self.rows {
            if row >= max_rows {
                // Scroll: remove top row, add blank row at bottom
                screen.remove(0);
                screen.push(String::new());
                // Cursor stays at bottom row (last row index)
                *self.cursor_row.borrow_mut() = max_rows - 1;
                return;
            }
        }

        // Ensure row exists (grow buffer if needed)
        while screen.len() <= row {
            screen.push(String::new());
        }
    }

    /// Write a string of regular characters at the current cursor position.
    ///
    /// In geometry mode, this handles automatic wrapping when writing past terminal width.
    /// In unbounded mode, the line grows indefinitely.
    pub(super) fn write_str(&self, s: &str) {
        if s.is_empty() {
            return;
        }

        // Handle wrapping character by character in geometry mode
        if self.cols.is_some() {
            for ch in s.chars() {
                self.write_char_with_wrap(ch);
            }
        } else {
            // Unbounded mode: write entire string without wrapping
            self.write_str_unbounded(s);
        }
    }

    /// Write a single character with automatic wrapping (geometry mode).
    fn write_char_with_wrap(&self, ch: char) {
        self.ensure_row_exists();
        let mut row = *self.cursor_row.borrow();
        let mut col = *self.cursor_col.borrow();

        // Check if we need to wrap to next line
        if let Some(max_cols) = self.cols {
            if col >= max_cols {
                // Wrap to next row, column 0
                *self.cursor_row.borrow_mut() = row + 1;
                *self.cursor_col.borrow_mut() = 0;
                self.ensure_row_exists(); // May trigger scrolling
                row = *self.cursor_row.borrow();
                col = 0;
            }
        }

        let screen = self.screen.borrow_mut();
        let line = &mut screen[row];

        // Extend the line with spaces if needed
        while line.chars().count() < col {
            line.push(' ');
        }

        // Build new line: prefix + new char + suffix
        let prefix: String = line.chars().take(col).collect();
        let suffix: String = line.chars().skip(col + 1).collect();
        *line = format!("{prefix}{ch}{suffix}");

        // Move cursor right
        *self.cursor_col.borrow_mut() = col + 1;
    }

    /// Write a string without wrapping (unbounded mode, backward compatible).
    fn write_str_unbounded(&self, s: &str) {
        self.ensure_row_exists();
        let row = *self.cursor_row.borrow();
        let col = *self.cursor_col.borrow();
        let screen = self.screen.borrow_mut();
        let line = &mut screen[row];

        // Extend the line with spaces if needed
        while line.chars().count() < col {
            line.push(' ');
        }

        // Build new line: prefix + new content + suffix
        let prefix: String = line.chars().take(col).collect();
        let suffix: String = line.chars().skip(col + s.chars().count()).collect();
        *line = format!("{prefix}{s}{suffix}");

        // Move cursor right
        *self.cursor_col.borrow_mut() = col + s.chars().count();
    }

    /// Clear the current line.
    ///
    /// This clears ONLY the current row where the cursor is positioned.
    /// If content has wrapped to multiple rows, this only clears the cursor's row.
    /// This behavior matches real terminal `\x1b[2K` (erase line).
    pub(super) fn clear_line(&self) {
        self.ensure_row_exists();
        let row = *self.cursor_row.borrow();
        let screen = self.screen.borrow_mut();
        screen[row].clear();
        // Note: cursor position is NOT changed by clear line
    }

    /// Move cursor up n rows.
    pub(super) fn cursor_up(&self, n: usize) {
        let mut row = self.cursor_row.borrow_mut();
        *row = row.saturating_sub(n);
    }

    /// Move cursor down n rows.
    pub(super) fn cursor_down(&self, n: usize) {
        *self.cursor_row.borrow_mut() += n;
        self.ensure_row_exists();
    }
}
