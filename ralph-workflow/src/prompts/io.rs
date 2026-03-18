//! I/O and boundary module for prompts - contains imperative parsing code.
//!
//! This module satisfies the dylint boundary-module check for code that uses
//! imperative patterns (while loops, mutable state, byte parsing).

use std::path::PathBuf;

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
// Serde serialization for prompt history types.
// =========================================================================

use crate::prompts::prompt_history_entry::PromptHistoryEntry;
use serde::{Serialize, Serializer};

impl Serialize for PromptHistoryEntry {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        use serde::ser::SerializeStruct;
        let mut s = serializer.serialize_struct(
            "PromptHistoryEntry",
            if self.content_id.is_some() { 2 } else { 1 },
        )?;
        s.serialize_field("content", &self.content)?;
        if let Some(content_id) = &self.content_id {
            s.serialize_field("content_id", content_id)?;
        }
        s.end()
    }
}

// =========================================================================
// Template parsing runtime - imperative byte-by-byte parsing.
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

fn parse_variable_spec(var_spec: &str) -> Option<(&str, Option<String>)> {
    let trimmed = var_spec.trim();
    if trimmed.starts_with('>') || trimmed.is_empty() {
        return None;
    }
    let (name, default_value) = var_spec.find('|').map_or((trimmed, None), |pipe_pos| {
        let name = var_spec[..pipe_pos].trim();
        let rest = &var_spec[pipe_pos + 1..];
        rest.find('=').map_or((name, None), |eq_pos| {
            let key = rest[..eq_pos].trim();
            if key == "default" {
                let value = rest[eq_pos + 1..].trim();
                let value = if (value.starts_with('"') && value.ends_with('"'))
                    || (value.starts_with('\'') && value.ends_with('\''))
                {
                    &value[1..value.len() - 1]
                } else {
                    value
                };
                (name, Some(value.to_string()))
            } else {
                (name, None)
            }
        })
    });
    Some((name, default_value))
}

#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
fn extract_variables_impl(content: &str) -> Vec<VariableInfo> {
    let bytes = content.as_bytes();
    let mut variables = Vec::new();
    let mut i = 0;
    let mut line = 0;

    while i < bytes.len().saturating_sub(1) {
        if bytes[i] == b'\n' {
            line += 1;
        }

        if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
            match skip_comment(bytes, i) {
                Some(next) => i = next,
                None => break,
            }
            continue;
        }

        if bytes[i] == b'{' && i + 1 < bytes.len() && bytes[i + 1] == b'{' {
            i += 2;

            while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                i += 1;
            }

            let name_start = i;

            while i < bytes.len()
                && !(bytes[i] == b'}' && i + 1 < bytes.len() && bytes[i + 1] == b'}')
            {
                i += 1;
            }

            if i < bytes.len() && bytes[i] == b'}' && i + 1 < bytes.len() && bytes[i + 1] == b'}' {
                let var_spec = &content[name_start..i];
                if let Some((var_name, default_value)) = parse_variable_spec(var_spec) {
                    variables.push(VariableInfo {
                        name: var_name.to_string(),
                        line,
                        has_default: default_value.is_some(),
                        default_value,
                    });
                }
                i += 2;
                continue;
            }
        }

        i += 1;
    }

    variables
}

fn skip_comment_partial(bytes: &[u8], start: usize) -> Option<usize> {
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

#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
fn extract_partials_impl(content: &str) -> Vec<String> {
    let bytes = content.as_bytes();
    let mut partials = Vec::new();
    let mut i = 0;

    while i < bytes.len().saturating_sub(2) {
        if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
            match skip_comment_partial(bytes, i) {
                Some(next) => i = next,
                None => break,
            }
            continue;
        }

        if bytes[i] == b'{' && bytes[i + 1] == b'{' && i + 2 < bytes.len() {
            i += 2;

            while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                i += 1;
            }

            if i < bytes.len() && bytes[i] == b'>' {
                i += 1;

                while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                    i += 1;
                }

                let name_start = i;
                while i < bytes.len()
                    && !(bytes[i] == b'}' && i + 1 < bytes.len() && bytes[i + 1] == b'}')
                {
                    i += 1;
                }

                if i < bytes.len()
                    && bytes[i] == b'}'
                    && i + 1 < bytes.len()
                    && bytes[i + 1] == b'}'
                {
                    let name = content[name_start..i].trim();
                    if !name.is_empty() {
                        partials.push(name.to_string());
                    }
                    i += 2;
                    continue;
                }
            }
        }
        i += 1;
    }

    partials
}

fn parse_metadata_line(line: &str) -> Option<(Option<String>, Option<String>)> {
    let inner = line[2..line.len() - 2].trim();
    let version = inner.strip_prefix("Version:").map(|s| s.trim().to_string());
    let purpose = inner.strip_prefix("PURPOSE:").map(|s| s.trim().to_string());
    Some((version, purpose))
}

/// Extract all variable references from template content.
#[must_use]
pub fn extract_variables(content: &str) -> Vec<VariableInfo> {
    extract_variables_impl(content)
}

/// Extract all partial references from template content.
#[must_use]
pub fn extract_partials(content: &str) -> Vec<String> {
    extract_partials_impl(content)
}

/// Extract template metadata from header comments.
#[must_use]
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked string slicing"
)]
pub fn extract_metadata(content: &str) -> TemplateMetadata {
    let mut version = None;
    let mut purpose = None;

    for line in content.lines().take(50) {
        let line = line.trim();
        if !line.starts_with("{#") || !line.ends_with("#}") {
            continue;
        }

        if let Some((v, p)) = parse_metadata_line(line) {
            version = version.or(v);
            purpose = purpose.or(p);
        }
    }

    TemplateMetadata { version, purpose }
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
                && bytes[self.i + 6] == b'i'
                && bytes[self.i + 7] == b'f'
                && bytes[self.i + 8] == b'o'
                && bytes[self.i + 9] == b'r'
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
