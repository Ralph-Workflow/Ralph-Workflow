//! I/O and boundary module for prompts - contains imperative parsing code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! imperative patterns (while loops, mutable state, byte parsing).

use std::path::PathBuf;

mod template_parsing;

pub use template_parsing::{extract_metadata, extract_partials, extract_variables};

pub use crate::prompts::template_registry::TemplateError;
pub use crate::prompts::template_validator::TemplateMetadata;
pub use crate::prompts::template_validator::ValidationError;
pub use crate::prompts::template_validator::VariableInfo;

/// Get the XDG config home directory for user template overrides.
#[must_use]
pub fn get_xdg_config_home() -> Option<PathBuf> {
    std::env::var("XDG_CONFIG_HOME")
        .ok()
        .map(PathBuf::from)
        .or_else(|| {
            std::env::var("HOME")
                .ok()
                .map(|h| PathBuf::from(h).join(".config"))
        })
}

/// Check if a template file exists at the given path.
#[must_use]
pub fn template_exists(path: &PathBuf) -> bool {
    std::path::Path::exists(path.as_path())
}

/// Load a template from the given path.
pub fn load_template(path: &PathBuf) -> Result<String, String> {
    std::fs::read_to_string(path).map_err(|e| e.to_string())
}

pub fn validate_syntax(content: &str) -> Vec<ValidationError> {
    let bytes = content.as_bytes();
    SyntaxValidator::new(content).validate(bytes)
}

// =========================================================================
// Pure parsing helpers (policy extracted from boundary)
// =========================================================================

fn skip_comment(bytes: &[u8], start: usize) -> Option<usize> {
    let mut i = start + 2;
    while i + 1 < bytes.len() && !(bytes[i] == b'#' && bytes[i + 1] == b'}') {
        i += 1;
    }
    if i + 1 < bytes.len() {
        Some(i + 2)
    } else {
        None
    }
}

fn parse_conditional_header(bytes: &[u8], start: usize) -> Option<ConditionalResult> {
    if start.saturating_add(5) < bytes.len()
        && bytes[start] == b'{'
        && bytes[start + 1] == b'%'
        && bytes[start + 2] == b' '
        && bytes[start + 3] == b'i'
        && bytes[start + 4] == b'f'
        && bytes[start.saturating_add(5)] == b' '
    {
        let end = find_tag_end(bytes, start + 6)?;
        return Some(ConditionalResult::IfStart {
            condition: end,
            line: count_lines_before(bytes, start),
        });
    }
    if start.saturating_add(9) < bytes.len()
        && bytes[start] == b'{'
        && bytes[start + 1] == b'%'
        && bytes[start + 2] == b' '
        && bytes[start + 3] == b'e'
        && bytes[start + 4] == b'n'
        && bytes[start.saturating_add(5)] == b'd'
        && bytes[start + 6] == b'i'
        && bytes[start + 7] == b'f'
        && bytes[start + 8] == b' '
        && bytes[start.saturating_add(9)] == b'%'
    {
        return Some(ConditionalResult::IfEnd {
            next_pos: start.saturating_add(11),
        });
    }
    None
}

fn parse_loop_header(bytes: &[u8], start: usize) -> Option<LoopResult> {
    if start.saturating_add(6) < bytes.len()
        && bytes[start] == b'{'
        && bytes[start + 1] == b'%'
        && bytes[start + 2] == b' '
        && bytes[start + 3] == b'f'
        && bytes[start + 4] == b'o'
        && bytes[start.saturating_add(5)] == b'r'
        && bytes[start.saturating_add(6)] == b' '
    {
        let end = find_tag_end(bytes, start + 7)?;
        return Some(LoopResult::ForStart {
            header_end: end,
            line: count_lines_before(bytes, start),
        });
    }
    if start.saturating_add(10) < bytes.len()
        && bytes[start] == b'{'
        && bytes[start + 1] == b'%'
        && bytes[start + 2] == b' '
        && bytes[start + 3] == b'e'
        && bytes[start + 4] == b'n'
        && bytes[start.saturating_add(5)] == b'd'
        && bytes[start + 6] == b'f'
        && bytes[start + 7] == b'o'
        && bytes[start + 8] == b'r'
        && bytes[start.saturating_add(9)] == b' '
    {
        return Some(LoopResult::ForEnd {
            next_pos: start.saturating_add(12),
        });
    }
    None
}

fn find_tag_end(bytes: &[u8], start: usize) -> Option<usize> {
    let mut i = start;
    while i + 1 < bytes.len() && !(bytes[i] == b'%' && bytes[i + 1] == b'}') {
        i += 1;
    }
    if i + 1 < bytes.len() {
        Some(i)
    } else {
        None
    }
}

fn count_lines_before(bytes: &[u8], pos: usize) -> usize {
    bytes[..pos].iter().filter(|&&b| b == b'\n').count()
}

enum ConditionalResult {
    IfStart { condition: usize, line: usize },
    IfEnd { next_pos: usize },
}

enum LoopResult {
    ForStart { header_end: usize, line: usize },
    ForEnd { next_pos: usize },
}

// =========================================================================
// Thin boundary (wiring only)
// =========================================================================

struct SyntaxValidator<'a> {
    content: &'a str,
    errors: Vec<ValidationError>,
    line: usize,
    i: usize,
    conditional_stack: Vec<(usize, &'static str)>,
    loop_stack: Vec<(usize, &'static str)>,
}

impl<'a> SyntaxValidator<'a> {
    const fn new(content: &'a str) -> Self {
        Self {
            content,
            errors: Vec::new(),
            line: 0,
            i: 0,
            conditional_stack: Vec::new(),
            loop_stack: Vec::new(),
        }
    }

    #[expect(
        clippy::arithmetic_side_effects,
        reason = "bounds-checked index arithmetic"
    )]
    fn validate(mut self, bytes: &[u8]) -> Vec<ValidationError> {
        while self.i < bytes.len() {
            if bytes[self.i] == b'\n' {
                self.line = self.line.saturating_add(1);
            }
            if self.i.saturating_add(1) < bytes.len()
                && bytes[self.i] == b'{'
                && bytes[self.i + 1] == b'#'
            {
                match skip_comment(bytes, self.i) {
                    Some(next) => {
                        self.i = next;
                    }
                    None => {
                        self.errors
                            .push(ValidationError::UnclosedComment { line: self.line });
                        self.i = bytes.len();
                    }
                }
                continue;
            }
            if self.i.saturating_add(5) < bytes.len()
                && bytes[self.i] == b'{'
                && bytes[self.i + 1] == b'%'
                && bytes[self.i + 2] == b' '
                && bytes[self.i + 3] == b'i'
                && bytes[self.i + 4] == b'f'
                && bytes[self.i.saturating_add(5)] == b' '
            {
                match find_tag_end(bytes, self.i.saturating_add(6)) {
                    Some(cond_end) => {
                        let condition = self.content[self.i + 6..cond_end].trim();
                        if condition.is_empty()
                            || condition.contains('{')
                            || condition.contains('}')
                        {
                            self.errors.push(ValidationError::InvalidConditional {
                                line: self.line,
                                syntax: condition.to_string(),
                            });
                        }
                        self.conditional_stack.push((self.line, "if"));
                        self.i = cond_end.saturating_add(2);
                    }
                    None => {
                        self.errors
                            .push(ValidationError::UnclosedConditional { line: self.line });
                        self.i = bytes.len();
                    }
                }
                continue;
            }
            if self.i.saturating_add(9) < bytes.len()
                && bytes[self.i] == b'{'
                && bytes[self.i + 1] == b'%'
                && bytes[self.i + 2] == b' '
                && bytes[self.i + 3] == b'e'
                && bytes[self.i + 4] == b'n'
                && bytes[self.i.saturating_add(5)] == b'd'
                && bytes[self.i + 6] == b'i'
                && bytes[self.i + 7] == b'f'
                && bytes[self.i + 8] == b' '
                && bytes[self.i.saturating_add(9)] == b'%'
            {
                self.conditional_stack.pop();
                self.i = self.i.saturating_add(11);
                continue;
            }
            if self.i.saturating_add(6) < bytes.len()
                && bytes[self.i] == b'{'
                && bytes[self.i + 1] == b'%'
                && bytes[self.i + 2] == b' '
                && bytes[self.i + 3] == b'f'
                && bytes[self.i + 4] == b'o'
                && bytes[self.i.saturating_add(5)] == b'r'
                && bytes[self.i.saturating_add(6)] == b' '
            {
                match find_tag_end(bytes, self.i.saturating_add(7)) {
                    Some(header_end) => {
                        let condition = self.content[self.i + 7..header_end].trim();
                        if !condition.contains(" in ") || condition.split(" in ").count() != 2 {
                            self.errors.push(ValidationError::InvalidLoop {
                                line: self.line,
                                syntax: condition.to_string(),
                            });
                        }
                        self.loop_stack.push((self.line, "for"));
                        self.i = header_end.saturating_add(2);
                    }
                    None => {
                        self.errors
                            .push(ValidationError::UnclosedLoop { line: self.line });
                        self.i = bytes.len();
                    }
                }
                continue;
            }
            if self.i.saturating_add(10) < bytes.len()
                && bytes[self.i] == b'{'
                && bytes[self.i + 1] == b'%'
                && bytes[self.i + 2] == b' '
                && bytes[self.i + 3] == b'e'
                && bytes[self.i + 4] == b'n'
                && bytes[self.i.saturating_add(5)] == b'd'
                && bytes[self.i + 6] == b'f'
                && bytes[self.i + 7] == b'o'
                && bytes[self.i + 8] == b'r'
                && bytes[self.i.saturating_add(9)] == b' '
            {
                self.loop_stack.pop();
                self.i = self.i.saturating_add(12);
                continue;
            }
            self.i = self.i.saturating_add(1);
        }
        self.check_unclosed_blocks();
        self.errors
    }

    fn check_unclosed_blocks(&mut self) {
        if let Some((line, _)) = self.conditional_stack.first() {
            self.errors
                .push(ValidationError::UnclosedConditional { line: *line });
        }
        if let Some((line, _)) = self.loop_stack.first() {
            self.errors
                .push(ValidationError::UnclosedLoop { line: *line });
        }
    }
}
