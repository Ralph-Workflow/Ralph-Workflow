// Virtual terminal implementation for simulating real terminal behavior in tests.

#[cfg(any(test, feature = "test-utils"))]
mod ansi;
#[cfg(any(test, feature = "test-utils"))]
mod helpers;
#[cfg(any(test, feature = "test-utils"))]
mod state;

#[cfg(any(test, feature = "test-utils"))]
use helpers::{apply_cr_overwrite_semantics, strip_ansi_sequences};

#[cfg(any(test, feature = "test-utils"))]
#[derive(Debug)]
pub struct VirtualTerminal {
    screen: Vec<String>,
    cursor_row: usize,
    cursor_col: usize,
    simulated_is_terminal: bool,
    write_history: Vec<String>,
    cols: Option<usize>,
    rows: Option<usize>,
}

#[cfg(any(test, feature = "test-utils"))]
impl VirtualTerminal {
    #[must_use]
    pub fn new() -> Self {
        Self {
            screen: vec![String::new()],
            cursor_row: 0,
            cursor_col: 0,
            simulated_is_terminal: true,
            write_history: Vec::new(),
            cols: None,
            rows: None,
        }
    }

    #[must_use]
    pub fn new_with_terminal(is_terminal: bool) -> Self {
        Self {
            screen: vec![String::new()],
            cursor_row: 0,
            cursor_col: 0,
            simulated_is_terminal: is_terminal,
            write_history: Vec::new(),
            cols: None,
            rows: None,
        }
    }

    #[must_use]
    pub fn new_with_geometry(cols: usize, rows: usize) -> Self {
        Self {
            screen: vec![String::new()],
            cursor_row: 0,
            cursor_col: 0,
            simulated_is_terminal: true,
            write_history: Vec::new(),
            cols: Some(cols),
            rows: Some(rows),
        }
    }

    pub fn get_visible_output(&self) -> String {
        let screen = &self.screen;
        screen
            .iter()
            .map(|line| line.trim_end().to_string())
            .collect::<Vec<_>>()
            .join("\n")
    }

    pub fn get_visible_lines(&self) -> Vec<String> {
        self.screen
            .iter()
            .map(|line| line.trim_end().to_string())
            .filter(|line| !line.is_empty())
            .collect()
    }

    pub fn count_visible_lines(&self) -> usize {
        self.get_visible_lines().len()
    }

    pub fn get_screen_content(&self) -> Vec<String> {
        self.screen.clone()
    }

    pub fn get_screen_lines(&self) -> Vec<String> {
        self.get_screen_content()
    }

    pub fn get_write_history(&self) -> Vec<String> {
        self.write_history.clone()
    }

    pub fn cursor_position(&self) -> (usize, usize) {
        (self.cursor_row, self.cursor_col)
    }

    #[must_use]
    pub fn clear(self) -> Self {
        Self {
            screen: vec![String::new()],
            cursor_row: 0,
            cursor_col: 0,
            write_history: Vec::new(),
            ..self
        }
    }

    pub fn has_duplicate_lines(&self) -> bool {
        let lines = self.get_visible_lines();
        lines.windows(2).any(|w| !w[1].is_empty() && w[1] == w[0])
    }

    pub fn count_visible_pattern(&self, pattern: &str) -> usize {
        self.get_visible_output().matches(pattern).count()
    }

    pub fn get_visible_output_ansi_stripped(&self) -> String {
        let stripped_writes: Vec<String> = self
            .write_history
            .iter()
            .map(|write| strip_ansi_sequences(write))
            .collect();

        apply_cr_overwrite_semantics(&stripped_writes.join(""))
    }

    pub fn count_physical_rows(&self) -> usize {
        self.get_screen_lines().len()
    }

    pub fn would_cursor_up_leave_orphans(&self, content: &str) -> bool {
        self.cols.is_some_and(|cols| {
            let stripped = strip_ansi_sequences(content);

            debug_assert!(
                stripped.is_ascii(),
                "would_cursor_up_leave_orphans is width-approximate; tests should be ASCII-only"
            );

            let content_len = stripped.chars().count();
            let rows_needed = content_len.div_ceil(cols);
            rows_needed > 1
        })
    }

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
            self.write_history.len()
        )
    }

    pub fn has_waterfall_pattern(&self, prefix: &str) -> bool {
        self.get_visible_lines()
            .iter()
            .filter(|line| line.contains(prefix))
            .take(2)
            .count()
            > 1
    }

    pub fn write_char(self, c: char) -> Self {
        if c == '\r' {
            return Self {
                cursor_col: 0,
                ..self
            };
        }
        if c == '\n' {
            let new_row = self.cursor_row.saturating_add(1);
            return Self {
                cursor_row: new_row,
                cursor_col: 0,
                ..self
            }
            .ensure_row_exists();
        }
        if c == '\x1b' {
            return self;
        }
        let col = self.cursor_col;
        let term = self.ensure_row_exists();
        let row = term.cursor_row;
        let line = &term.screen[row];
        let current_len = line.chars().count();
        let new_line = if current_len < col {
            format!("{}{}", line, " ".repeat(col - current_len))
        } else {
            line.clone()
        };
        let prefix: String = new_line.chars().take(col).collect();
        let suffix: String = new_line.chars().skip(col + 1).collect();
        let updated_line = format!("{prefix}{c}{suffix}");
        let mut new_screen = term.screen.clone();
        new_screen[row] = updated_line;
        let Self {
            screen: _,
            cursor_row: cr,
            cursor_col: cc,
            simulated_is_terminal: sit,
            write_history: wh,
            cols,
            rows,
        } = term;
        VirtualTerminal {
            screen: new_screen,
            cursor_col: cc + 1,
            cursor_row: cr,
            simulated_is_terminal: sit,
            write_history: wh,
            cols,
            rows,
        }
    }

    fn ensure_row_exists(mut self) -> Self {
        if let Some(max_rows) = self.rows {
            if self.cursor_row >= max_rows {
                self.screen.remove(0);
                self.screen.push(String::new());
                self.cursor_row = max_rows - 1;
                return self;
            }
        }
        let needed_rows = self.cursor_row.saturating_add(1);
        if self.screen.len() < needed_rows {
            self.screen.resize(needed_rows, String::new());
        }
        self
    }

    #[must_use]
    pub fn write_str(self, s: &str) -> Self {
        if s.is_empty() {
            return self;
        }
        if self.cols.is_some() {
            s.chars().fold(self, |term, ch| term.write_char(ch))
        } else {
            let row = self.cursor_row;
            let col = self.cursor_col;
            let term = self.ensure_row_exists();
            let line = &term.screen[row];
            let current_len = line.chars().count();
            let new_line = if current_len < col {
                format!("{}{}", line, " ".repeat(col - current_len))
            } else {
                line.clone()
            };
            let prefix: String = new_line.chars().take(col).collect();
            let suffix: String = new_line.chars().skip(col + s.chars().count()).collect();
            let updated_line = format!("{}{}{}", prefix, s, suffix);
            let mut new_screen = term.screen.clone();
            new_screen[row] = updated_line;
            VirtualTerminal {
                screen: new_screen,
                cursor_col: col + s.chars().count(),
                ..term
            }
        }
    }

    #[must_use]
    pub fn process_string(self, s: &str) -> Self {
        let chars: Vec<char> = s.chars().collect();
        let (term, text_buffer, _) = chars.iter().enumerate().fold(
            (self, String::new(), 0),
            |(mut term, mut text_buffer, _i), (idx, &c)| match c {
                '\r' => {
                    if !text_buffer.is_empty() {
                        term = term.write_str(&text_buffer);
                        text_buffer.clear();
                    }
                    term.cursor_col = 0;
                    (term, text_buffer, idx + 1)
                }
                '\n' => {
                    if !text_buffer.is_empty() {
                        term = term.write_str(&text_buffer);
                        text_buffer.clear();
                    }
                    term.cursor_row = term.cursor_row.saturating_add(1);
                    term.cursor_col = 0;
                    term = term.ensure_row_exists();
                    (term, text_buffer, idx + 1)
                }
                '\x1b' => {
                    if !text_buffer.is_empty() {
                        term = term.write_str(&text_buffer);
                        text_buffer.clear();
                    }
                    if idx + 1 < chars.len() && chars[idx + 1] == '[' {
                        let param_end = chars[idx + 2..]
                            .iter()
                            .position(|&c| !c.is_ascii_digit())
                            .map(|p| idx + 2 + p)
                            .unwrap_or(chars.len());
                        let param: String = chars[idx + 2..param_end].iter().collect();
                        let cmd_i = param_end;
                        if cmd_i < chars.len() {
                            let cmd = chars[cmd_i];
                            let n: usize = param.parse().unwrap_or(1);
                            match cmd {
                                'A' => {
                                    term.cursor_row = term.cursor_row.saturating_sub(n);
                                    (term, text_buffer, idx + 1)
                                }
                                'B' => {
                                    term.cursor_row += n;
                                    term = term.ensure_row_exists();
                                    (term, text_buffer, idx + 1)
                                }
                                'K' => {
                                    let mode: usize = param.parse().unwrap_or(0);
                                    if mode == 2 {
                                        term = term.clear_line();
                                    }
                                    (term, text_buffer, idx + 1)
                                }
                                _ => (term, text_buffer, idx + 1),
                            }
                        } else {
                            (term, text_buffer, idx)
                        }
                    } else {
                        (term, text_buffer, idx + 1)
                    }
                }
                _ => {
                    text_buffer.push(c);
                    (term, text_buffer, idx + 1)
                }
            },
        );

        if !text_buffer.is_empty() {
            term.write_str(&text_buffer)
        } else {
            term
        }
    }

    #[must_use]
    fn clear_line(self) -> Self {
        let row = self.cursor_row;
        let term = self.ensure_row_exists();
        let Self {
            mut screen,
            cursor_row,
            cursor_col,
            simulated_is_terminal,
            write_history,
            cols,
            rows,
        } = term;
        screen[row] = String::new();
        VirtualTerminal {
            screen,
            cursor_row,
            cursor_col,
            simulated_is_terminal,
            write_history,
            cols,
            rows,
        }
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

        self.write_history.push(s.to_string());
        let new_self = std::mem::take(self).process_string(s);
        *self = new_self;

        Ok(buf.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl Printable for VirtualTerminal {
    fn is_terminal(&self) -> bool {
        self.simulated_is_terminal
    }
}
