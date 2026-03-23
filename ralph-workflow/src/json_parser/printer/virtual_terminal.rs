#[cfg(any(test, feature = "test-utils"))]
fn strip_ansi_sequences(s: &str) -> String {
    s.chars()
        .fold((String::new(), false), |(mut result, in_esc), c| {
            if c == '\x1b' {
                (result, true)
            } else if in_esc {
                if !c.is_ascii_alphabetic() {
                    (result, true)
                } else {
                    (result, false)
                }
            } else {
                result.push(c);
                (result, false)
            }
        })
        .0
}

#[cfg(any(test, feature = "test-utils"))]
fn apply_cr_overwrite_semantics(s: &str) -> String {
    let mut out = String::new();
    let mut line: Vec<char> = Vec::new();
    let mut col = 0usize;

    for ch in s.chars() {
        match ch {
            '\n' => {
                out.extend(line.iter());
                out.push('\n');
                line.clear();
                col = 0;
            }
            '\r' => {
                col = 0;
            }
            _ => {
                if col < line.len() {
                    line[col] = ch;
                } else {
                    if line.len() < col {
                        line.resize(col, ' ');
                    }
                    line.push(ch);
                }
                col = col.saturating_add(1);
            }
        }
    }

    out.extend(line.iter());
    out
}

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
        let mut term = self;
        let mut text_buffer = String::new();
        let mut idx = 0;

        fn parse_csi_param_usize(param: &str, default: usize) -> usize {
            param
                .split(';')
                .next()
                .and_then(|p| {
                    if p.is_empty() {
                        None
                    } else {
                        p.parse::<usize>().ok()
                    }
                })
                .unwrap_or(default)
        }

        fn is_csi_final_byte(c: char) -> bool {
            ('@'..='~').contains(&c)
        }

        while idx < chars.len() {
            let c = chars[idx];
            match c {
                '\r' => {
                    if !text_buffer.is_empty() {
                        term = term.write_str(&text_buffer);
                        text_buffer.clear();
                    }
                    term.cursor_col = 0;
                    idx += 1;
                }
                '\n' => {
                    if !text_buffer.is_empty() {
                        term = term.write_str(&text_buffer);
                        text_buffer.clear();
                    }
                    term.cursor_row = term.cursor_row.saturating_add(1);
                    term.cursor_col = 0;
                    term = term.ensure_row_exists();
                    idx += 1;
                }
                '\x1b' => {
                    if !text_buffer.is_empty() {
                        term = term.write_str(&text_buffer);
                        text_buffer.clear();
                    }
                    if idx + 1 < chars.len() && chars[idx + 1] == '[' {
                        let mut cmd_i = idx + 2;
                        while cmd_i < chars.len() && !is_csi_final_byte(chars[cmd_i]) {
                            cmd_i += 1;
                        }

                        if cmd_i < chars.len() {
                            let cmd = chars[cmd_i];
                            let param: String = chars[idx + 2..cmd_i].iter().collect();
                            idx = cmd_i + 1;

                            match cmd {
                                'A' => {
                                    let n = parse_csi_param_usize(&param, 1);
                                    term.cursor_row = term.cursor_row.saturating_sub(n);
                                }
                                'B' => {
                                    let n = parse_csi_param_usize(&param, 1);
                                    term.cursor_row += n;
                                    term = term.ensure_row_exists();
                                }
                                'K' => {
                                    let mode = parse_csi_param_usize(&param, 0);
                                    if mode == 2 {
                                        term = term.clear_line();
                                    }
                                }
                                'm' => {
                                    // SGR (color codes) - consume without tracking state
                                }
                                _ => {} // Unknown CSI - consume without action
                            }
                        } else {
                            text_buffer.push(c);
                            idx += 1;
                        }
                    } else {
                        text_buffer.push(c);
                        idx += 1;
                    }
                }
                _ => {
                    text_buffer.push(c);
                    idx += 1;
                }
            }
        }

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

#[cfg(test)]
mod virtual_terminal_pure_tests {
    use super::{apply_cr_overwrite_semantics, strip_ansi_sequences};

    #[test]
    fn strip_ansi_sequences_removes_sgr_codes() {
        let input = "\x1b[32mGreen\x1b[0m Normal";
        assert_eq!(strip_ansi_sequences(input), "Green Normal");
    }

    #[test]
    fn apply_cr_overwrite_semantics_overwrites_current_line() {
        let input = "Hello\rWorld";
        assert_eq!(apply_cr_overwrite_semantics(input), "World");
    }
}
