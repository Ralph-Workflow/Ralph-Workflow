//! Pure domain helpers for template parsing and formatting.
//!
//! This module contains the actual parsing and formatting logic extracted from the boundary
//! modules. These functions are pure and do not perform I/O.

use crate::checkpoint::execution_history::{IssuesSummary, ModifiedFilesDetail};
use crate::prompts::template_validator::VariableInfo;
use std::collections::HashMap;

/// Parse a variable specification string into name and optional default value.
///
/// Pure domain function - no I/O or side effects.
pub fn parse_variable_spec_impl(var_spec: &str) -> Option<(&str, Option<String>)> {
    let trimmed = var_spec.trim();
    if trimmed.starts_with('>') || trimmed.is_empty() {
        return None;
    }
    let (name, default_value) = trimmed.find('|').map_or((trimmed, None), |pipe_pos| {
        let name = trimmed[..pipe_pos].trim();
        let rest = &trimmed[pipe_pos + 1..];
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

/// Parse a metadata line from template header comments.
///
/// Pure domain function - no I/O or side effects.
pub fn parse_metadata_line_impl(line: &str) -> Option<(Option<String>, Option<String>)> {
    let inner = line[2..line.len() - 2].trim();
    let version = inner.strip_prefix("Version:").map(|s| s.trim().to_string());
    let purpose = inner.strip_prefix("PURPOSE:").map(|s| s.trim().to_string());
    Some((version, purpose))
}

/// Extract variable info from a parsed variable spec.
///
/// Pure domain function.
pub fn make_variable_info(
    var_name: &str,
    line: usize,
    default_value: Option<String>,
) -> VariableInfo {
    VariableInfo {
        name: var_name.to_string(),
        line,
        has_default: default_value.is_some(),
        default_value,
    }
}

/// Check if a line is a valid metadata comment line.
///
/// Pure domain function.
pub fn is_metadata_comment(line: &str) -> bool {
    line.trim().starts_with("{#") && line.trim().ends_with("#}")
}

/// Parse loop header to extract variable name and body.
///
/// Returns (var_name, body) if parsing succeeds.
///
/// Pure domain function.
pub fn parse_loop_header_impl(full_match: &str) -> Option<(&str, &str)> {
    let in_pos = full_match.find(" in ")?;
    let header = &full_match[7..in_pos];
    let body_start = full_match.find("%}").map_or(full_match.len(), |p| p + 2);
    let body = &full_match[body_start..full_match.len() - 2];
    Some((header, body))
}

/// Split loop items by comma or line.
///
/// Pure domain function.
pub fn split_loop_items_impl(values: &str) -> Vec<&str> {
    if values.contains(',') {
        values.split(',').map(str::trim).collect()
    } else {
        values
            .lines()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .collect()
    }
}

/// Evaluate a conditional expression against variables.
pub fn eval_conditional_impl(condition: &str, variables: &HashMap<&str, String>) -> bool {
    variables
        .get(condition)
        .map(|v| !v.is_empty())
        .unwrap_or(false)
}

pub fn format_resume_state_impl(resume_count: u32, rebase_state: &str) -> String {
    match (resume_count > 0, rebase_state != "NotStarted") {
        (true, true) => format!(
            "This session has been resumed {resume_count} time(s)\nRebase state: {rebase_state}"
        ),
        (true, false) => format!("This session has been resumed {resume_count} time(s)\n"),
        (false, true) => format!("Rebase state: {rebase_state}"),
        (false, false) => String::new(),
    }
}

pub fn format_files_summary_impl(detail: &ModifiedFilesDetail) -> Option<String> {
    let added_count = detail.added.as_ref().map_or(0, |v| v.len());
    let modified_count = detail.modified.as_ref().map_or(0, |v| v.len());
    let deleted_count = detail.deleted.as_ref().map_or(0, |v| v.len());
    let total_files = added_count + modified_count + deleted_count;
    if total_files == 0 {
        return None;
    }

    let s = format!("  Files: {total_files} changed");
    let s = match (added_count > 0, modified_count > 0, deleted_count > 0) {
        (true, true, true) => format!(
            "{s} ({added_count} added) ({modified_count} modified) ({deleted_count} deleted)"
        ),
        (true, true, false) => format!("{s} ({added_count} added) ({modified_count} modified)"),
        (true, false, true) => format!("{s} ({added_count} added) ({deleted_count} deleted)"),
        (true, false, false) => format!("{s} ({added_count} added)"),
        (false, true, true) => format!("{s} ({modified_count} modified) ({deleted_count} deleted)"),
        (false, true, false) => format!("{s} ({modified_count} modified)"),
        (false, false, true) => format!("{s} ({deleted_count} deleted)"),
        (false, false, false) => s,
    };
    Some(format!("{s}\n"))
}

pub fn format_issues_summary_impl(issues: &IssuesSummary) -> Option<String> {
    if issues.found == 0 && issues.fixed == 0 {
        return None;
    }

    let s = format!("  Issues: {} found, {} fixed", issues.found, issues.fixed);
    let s = issues
        .description
        .as_ref()
        .map_or(s.as_str(), |desc| format!("{s} ({desc})"));
    Some(format!("{s}\n"))
}

// ============================================================================
// Pure extraction helpers - extracted from io.rs boundary
// ============================================================================

fn is_var_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_' || b == b'-'
}

fn find_var_end(bytes: &[u8], start: usize) -> usize {
    start
        + bytes[start..]
            .iter()
            .take_while(|&&b| is_var_char(b))
            .count()
}

fn skip_whitespace(bytes: &[u8], start: usize) -> usize {
    start
        + bytes[start..]
            .iter()
            .take_while(|&&b| b == b' ' || b == b'\t')
            .count()
}

fn find_comment_end(bytes: &[u8], start: usize) -> Option<usize> {
    bytes[start + 2..]
        .windows(2)
        .position(|w| w[0] == b'#' && w[1] == b'}')
        .map(|i| start + i + 4)
}

fn find_tag_end(bytes: &[u8], start: usize) -> Option<usize> {
    bytes[start..]
        .windows(2)
        .position(|w| w[0] == b'%' && w[1] == b'}')
        .map(|i| start + i)
}

fn skip_comment(bytes: &[u8], start: usize) -> Option<usize> {
    find_comment_end(bytes, start)
}

fn skip_comment_partial(bytes: &[u8], start: usize) -> Option<usize> {
    find_comment_end(bytes, start)
}

pub fn extract_variables_impl(content: &str) -> Vec<VariableInfo> {
    extract_vars_recursive(content.as_bytes(), 0, 0)
}

fn extract_vars_recursive(bytes: &[u8], i: usize, line: usize) -> Vec<VariableInfo> {
    if i >= bytes.len().saturating_sub(1) {
        return Vec::new();
    }

    if bytes[i] == b'\n' {
        return extract_vars_recursive(bytes, i + 1, line + 1);
    }

    if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
        match find_comment_end(bytes, i) {
            Some(next) => extract_vars_recursive(bytes, next, line),
            None => Vec::new(),
        }
    } else if bytes[i] == b'{' && i + 1 < bytes.len() && bytes[i + 1] == b'{' {
        let j = i + 2;
        let j = skip_whitespace(bytes, j);
        let name_start = j;
        let j = skip_whitespace(bytes, j);

        let var_end = find_var_end(bytes, j);
        if var_end > j {
            let mut result = Vec::new();
            if let Some((var_name, default_value)) =
                std::str::from_utf8(&bytes[name_start..var_end])
                    .ok()
                    .and_then(parse_variable_spec_impl)
            {
                result.push(VariableInfo {
                    name: var_name.to_string(),
                    line,
                    has_default: default_value.is_some(),
                    default_value,
                });
            }
            result.extend(extract_vars_recursive(bytes, var_end + 2, line));
            result
        } else {
            extract_vars_recursive(bytes, i + 1, line)
        }
    } else {
        extract_vars_recursive(bytes, i + 1, line)
    }
}

pub fn extract_partials_impl(content: &str) -> Vec<String> {
    extract_partials_recursive(content.as_bytes(), 0)
}

fn extract_partials_recursive(bytes: &[u8], i: usize) -> Vec<String> {
    if i >= bytes.len().saturating_sub(2) {
        return Vec::new();
    }

    if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
        match skip_comment_partial(bytes, i) {
            Some(next) => extract_partials_recursive(bytes, next),
            None => Vec::new(),
        }
    } else if bytes[i] == b'{' && bytes[i + 1] == b'{' && i + 2 < bytes.len() {
        let j = i + 2;

        let j = j + bytes[j..]
            .iter()
            .take_while(|&&b| b == b' ' || b == b'\t')
            .count();

        if j < bytes.len() && bytes[j] == b'>' {
            let j = j + 1;

            let j = j + bytes[j..]
                .iter()
                .take_while(|&&b| b == b' ' || b == b'\t')
                .count();

            let name_start = j;

            let j = j + bytes[j..]
                .iter()
                .take_while(|&&b| {
                    b != b'}' && !(b == b'}' && j + 1 < bytes.len() && bytes[j + 1] == b'}')
                })
                .count();

            if j < bytes.len() && bytes[j] == b'}' && j + 1 < bytes.len() && bytes[j + 1] == b'}' {
                let mut result = Vec::new();
                let name = content[name_start..j].trim();
                if !name.is_empty() {
                    result.push(name.to_string());
                }
                result.extend(extract_partials_recursive(bytes, j + 2));
                return result;
            }
        }

        extract_partials_recursive(bytes, i + 1)
    } else {
        extract_partials_recursive(bytes, i + 1)
    }
}

#[derive(Debug, Clone)]
pub enum ValidationError {
    UnclosedComment { line: usize },
    UnclosedConditional { line: usize },
    UnclosedLoop { line: usize },
    InvalidConditional { line: usize, syntax: String },
    InvalidLoop { line: usize, syntax: String },
}

pub struct ValidationState {
    pub errors: Vec<ValidationError>,
    pub conditional_stack: Vec<(usize, &'static str)>,
    pub loop_stack: Vec<(usize, &'static str)>,
}

impl Default for ValidationState {
    fn default() -> Self {
        Self {
            errors: Vec::new(),
            conditional_stack: Vec::new(),
            loop_stack: Vec::new(),
        }
    }
}

pub fn validate_template_bytes(content: &str, bytes: &[u8]) -> ValidationState {
    bytes
        .iter()
        .enumerate()
        .fold(ValidationState::default(), |state, (i, &byte)| {
            process_byte(content, bytes, state, i, byte)
        })
}

fn process_byte(
    content: &str,
    bytes: &[u8],
    mut state: ValidationState,
    i: usize,
    byte: u8,
) -> ValidationState {
    if i >= bytes.len() {
        if let Some((line, _)) = state.conditional_stack.first() {
            state
                .errors
                .push(ValidationError::UnclosedConditional { line: *line });
        }
        if let Some((line, _)) = state.loop_stack.first() {
            state
                .errors
                .push(ValidationError::UnclosedLoop { line: *line });
        }
        return state;
    }

    if bytes[i] == b'\n' {
        state = process_byte(content, bytes, state, i + 1, bytes[i]);
        return state;
    }

    if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
        match skip_comment(bytes, i) {
            Some(next) => {
                state = process_byte(content, bytes, state, next, bytes[next]);
                return state;
            }
            None => {
                state
                    .errors
                    .push(ValidationError::UnclosedComment { line: 0 });
                return state;
            }
        }
    }

    if i + 5 < bytes.len()
        && bytes[i] == b'{'
        && bytes[i + 1] == b'%'
        && bytes[i + 2] == b' '
        && bytes[i + 3] == b'i'
        && bytes[i + 4] == b'f'
        && bytes[i + 5] == b' '
    {
        match find_tag_end(bytes, i + 6) {
            Some(cond_end) => {
                let condition = &content[i + 6..cond_end].trim();
                if condition.is_empty() || condition.contains('{') || condition.contains('}') {
                    state.errors.push(ValidationError::InvalidConditional {
                        line: 0,
                        syntax: condition.to_string(),
                    });
                }
                state.conditional_stack.push((0, "if"));
                state = process_byte(content, bytes, state, cond_end + 2, bytes[cond_end + 2]);
                return state;
            }
            None => {
                state
                    .errors
                    .push(ValidationError::UnclosedConditional { line: 0 });
                return state;
            }
        }
    }

    if i + 9 < bytes.len()
        && bytes[i] == b'{'
        && bytes[i + 1] == b'%'
        && bytes[i + 2] == b' '
        && bytes[i + 3] == b'e'
        && bytes[i + 4] == b'n'
        && bytes[i + 5] == b'd'
        && bytes[i + 6] == b'i'
        && bytes[i + 7] == b'f'
        && bytes[i + 8] == b' '
        && bytes[i + 9] == b'%'
    {
        state.conditional_stack.pop();
        state = process_byte(content, bytes, state, i + 11, bytes[i + 11]);
        return state;
    }

    if i + 6 < bytes.len()
        && bytes[i] == b'{'
        && bytes[i + 1] == b'%'
        && bytes[i + 2] == b' '
        && bytes[i + 3] == b'f'
        && bytes[i + 4] == b'o'
        && bytes[i + 5] == b'r'
        && bytes[i + 6] == b' '
    {
        match find_tag_end(bytes, i + 7) {
            Some(header_end) => {
                let condition = &content[i + 7..header_end].trim();
                if !condition.contains(" in ") || condition.split(" in ").count() != 2 {
                    state.errors.push(ValidationError::InvalidLoop {
                        line: 0,
                        syntax: condition.to_string(),
                    });
                }
                state.loop_stack.push((0, "for"));
                state = process_byte(content, bytes, state, header_end + 2, bytes[header_end + 2]);
                return state;
            }
            None => {
                state.errors.push(ValidationError::UnclosedLoop { line: 0 });
                return state;
            }
        }
    }

    if i + 10 < bytes.len()
        && bytes[i] == b'{'
        && bytes[i + 1] == b'%'
        && bytes[i + 2] == b' '
        && bytes[i + 3] == b'e'
        && bytes[i + 4] == b'n'
        && bytes[i + 5] == b'd'
        && bytes[i + 6] == b'i'
        && bytes[i + 7] == b'f'
        && bytes[i + 8] == b'o'
        && bytes[i + 9] == b'r'
        && bytes[i + 10] == b' '
    {
        state.loop_stack.pop();
        state = process_byte(content, bytes, state, i + 12, bytes[i + 12]);
        return state;
    }

    state
}

const PARTIAL_FIELD_MAX_CHARS: usize = 120;

fn truncate_one_line(input: &str, max_chars: usize) -> String {
    let first_line = input.lines().next().unwrap_or("").trim();
    let out: String = first_line.chars().take(max_chars).collect();
    if first_line.chars().count() > max_chars {
        format!("{}...(truncated)", out)
    } else {
        out
    }
}

#[derive(Debug, Clone)]
pub struct OutcomeDescription {
    pub success_with_output: Option<String>,
    pub success_with_files: Option<String>,
    pub success_plain: String,
    pub failure_recoverable: Option<String>,
    pub failure_fatal: Option<String>,
    pub partial: Option<String>,
    pub skipped: Option<String>,
}

impl OutcomeDescription {
    pub fn from_outcome(
        files_modified: &Option<Vec<String>>,
        output: &Option<String>,
        error: &Option<String>,
        recoverable: &Option<bool>,
        completed: &Option<String>,
        remaining: &Option<String>,
        reason: &Option<String>,
    ) -> Self {
        let success_with_output = output.as_ref().and_then(|out| {
            if out.is_empty() {
                None
            } else {
                Some(format!("Success - {}", out.lines().next().unwrap_or("")))
            }
        });
        let success_with_files = files_modified.as_ref().and_then(|files| {
            if files.is_empty() {
                None
            } else {
                Some(format!("Success - {} files modified", files.len()))
            }
        });
        let success_plain = "Success".to_string();
        let failure_recoverable = error.as_ref().and_then(|e| {
            recoverable.and_then(|r| {
                if r {
                    Some(format!(
                        "Recoverable error - {}",
                        e.lines().next().unwrap_or("")
                    ))
                } else {
                    None
                }
            })
        });
        let failure_fatal = error.as_ref().and_then(|e| {
            if recoverable.unwrap_or(true) {
                None
            } else {
                Some(format!("Failed - {}", e.lines().next().unwrap_or("")))
            }
        });
        let partial = match (completed.as_ref(), remaining.as_ref()) {
            (Some(c), Some(r)) => {
                let c = truncate_one_line(c, PARTIAL_FIELD_MAX_CHARS);
                let r = truncate_one_line(r, PARTIAL_FIELD_MAX_CHARS);
                Some(format!("Partial - {c} done, {r}"))
            }
            _ => None,
        };
        let skipped = reason.as_ref().map(|r| format!("Skipped - {r}"));
        Self {
            success_with_output,
            success_with_files,
            success_plain,
            failure_recoverable,
            failure_fatal,
            partial,
            skipped,
        }
    }

    pub fn as_string(&self) -> String {
        self.success_with_output
            .clone()
            .or_else(|| self.success_with_files.clone())
            .unwrap_or_else(|| self.success_plain.clone())
    }
}
