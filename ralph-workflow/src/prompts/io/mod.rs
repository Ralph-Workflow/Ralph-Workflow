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

    fn validate(mut self, bytes: &[u8]) -> Vec<ValidationError> {
        while self.i < bytes.len() {
            self.track_newlines(bytes);
            if self.try_skip_comment(bytes) {
                continue;
            }
            if self.try_parse_conditional(bytes) {
                continue;
            }
            if self.try_parse_loop(bytes) {
                continue;
            }
            self.i = self.i.saturating_add(1);
        }
        self.check_unclosed_blocks();
        self.errors
    }

    fn track_newlines(&mut self, bytes: &[u8]) {
        if bytes[self.i] == b'\n' {
            self.line = self.line.saturating_add(1);
        }
    }

    fn try_skip_comment(&mut self, bytes: &[u8]) -> bool {
        if self.i.saturating_add(1) < bytes.len()
            && bytes[self.i] == b'{'
            && bytes[self.i + 1] == b'#'
        {
            let comment_start = self.line;
            self.i = self.i.saturating_add(2);
            while self.i.saturating_add(1) < bytes.len()
                && !(bytes[self.i] == b'#' && bytes[self.i + 1] == b'}')
            {
                if bytes[self.i] == b'\n' {
                    self.line = self.line.saturating_add(1);
                }
                self.i = self.i.saturating_add(1);
            }
            if self.i.saturating_add(1) >= bytes.len() {
                self.errors.push(ValidationError::UnclosedComment {
                    line: comment_start,
                });
            }
            if self.i.saturating_add(1) < bytes.len() {
                self.i = self.i.saturating_add(2);
            }
            true
        } else {
            false
        }
    }

    fn try_parse_conditional(&mut self, bytes: &[u8]) -> bool {
        if self.i.saturating_add(5) < bytes.len()
            && bytes[self.i] == b'{'
            && bytes[self.i + 1] == b'%'
            && bytes[self.i + 2] == b' '
            && bytes[self.i + 3] == b'i'
            && bytes[self.i + 4] == b'f'
            && bytes[self.i.saturating_add(5)] == b' '
        {
            let if_start = self.i;
            self.i = self.i.saturating_add(6);
            while self.i + 1 < bytes.len() && !(bytes[self.i] == b'%' && bytes[self.i + 1] == b'}')
            {
                self.i = self.i.saturating_add(1);
            }
            if self.i + 1 >= bytes.len() {
                self.errors
                    .push(ValidationError::UnclosedConditional { line: self.line });
            } else {
                let condition = self.content[if_start + 6..self.i].trim();
                if condition.is_empty() || condition.contains('{') || condition.contains('}') {
                    self.errors.push(ValidationError::InvalidConditional {
                        line: self.line,
                        syntax: condition.to_string(),
                    });
                }
                self.conditional_stack.push((self.line, "if"));
                self.i = self.i.saturating_add(2);
            }
            return true;
        }

        if self.i.saturating_add(9) < bytes.len()
            && bytes[self.i] == b'{'
            && bytes[self.i + 1] == b'%'
            && bytes[self.i + 2] == b' '
            && bytes[self.i + 3] == b'e'
            && bytes[self.i + 4] == b'n'
            && bytes[self.i.saturating_add(5)] == b'd'
            && bytes[self.i.saturating_add(6)] == b'i'
            && bytes[self.i + 7] == b'f'
            && bytes[self.i + 8] == b' '
            && bytes[self.i.saturating_add(9)] == b'%'
        {
            self.conditional_stack.pop();
            self.i = self.i.saturating_add(11);
            return true;
        }

        false
    }

    fn try_parse_loop(&mut self, bytes: &[u8]) -> bool {
        if self.i.saturating_add(6) < bytes.len()
            && bytes[self.i] == b'{'
            && bytes[self.i + 1] == b'%'
            && bytes[self.i + 2] == b' '
            && bytes[self.i + 3] == b'f'
            && bytes[self.i + 4] == b'o'
            && bytes[self.i.saturating_add(5)] == b'r'
            && bytes[self.i.saturating_add(6)] == b' '
        {
            let for_start = self.i;
            self.i = self.i.saturating_add(7);
            while self.i + 1 < bytes.len() && !(bytes[self.i] == b'%' && bytes[self.i + 1] == b'}')
            {
                self.i = self.i.saturating_add(1);
            }
            if self.i + 1 >= bytes.len() {
                self.errors
                    .push(ValidationError::UnclosedLoop { line: self.line });
            } else {
                let condition = self.content[for_start + 7..self.i].trim();
                if !condition.contains(" in ") || condition.split(" in ").count() != 2 {
                    self.errors.push(ValidationError::InvalidLoop {
                        line: self.line,
                        syntax: condition.to_string(),
                    });
                }
                self.loop_stack.push((self.line, "for"));
                self.i = self.i.saturating_add(2);
            }
            return true;
        }

        if self.i.saturating_add(10) < bytes.len()
            && bytes[self.i] == b'{'
            && bytes[self.i + 1] == b'%'
            && bytes[self.i + 2] == b' '
            && bytes[self.i + 3] == b'e'
            && bytes[self.i + 4] == b'n'
            && bytes[self.i.saturating_add(5)] == b'd'
            && bytes[self.i.saturating_add(6)] == b'f'
            && bytes[self.i + 7] == b'o'
            && bytes[self.i + 8] == b'r'
            && bytes[self.i.saturating_add(9)] == b' '
        {
            self.loop_stack.pop();
            self.i = self.i.saturating_add(12);
            return true;
        }

        false
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
