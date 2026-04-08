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
/// The line is expected to be a Jinja2-style comment of the form `{# ... #}`.
/// Returns `None` for lines shorter than 4 bytes or with invalid byte boundaries.
///
/// Pure domain function - no I/O or side effects.
pub fn parse_metadata_line_impl(line: &str) -> Option<(Option<String>, Option<String>)> {
    let inner = line.get(2..line.len().saturating_sub(2))?.trim();
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
        placeholder: var_name.to_string(),
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
    let parts: Vec<String> = [
        (added_count, "added"),
        (modified_count, "modified"),
        (deleted_count, "deleted"),
    ]
    .iter()
    .filter_map(|&(count, label)| {
        if count > 0 {
            Some(format!("({count} {label})"))
        } else {
            None
        }
    })
    .collect();
    let suffix = if parts.is_empty() {
        String::new()
    } else {
        format!(" {}", parts.join(" "))
    };
    Some(format!("{s}{suffix}\n"))
}

pub fn format_issues_summary_impl(issues: &IssuesSummary) -> Option<String> {
    if issues.found == 0 && issues.fixed == 0 {
        return None;
    }

    let s = format!("  Issues: {} found, {} fixed", issues.found, issues.fixed);
    let s = issues
        .description
        .as_ref()
        .map_or_else(|| s.clone(), |desc| format!("{s} ({desc})"));
    Some(format!("{s}\n"))
}

// ============================================================================
// Pure extraction helpers - extracted from io.rs boundary
// ============================================================================

fn find_variable_end(bytes: &[u8], start: usize) -> Option<usize> {
    bytes[start..]
        .windows(2)
        .position(|w| w[0] == b'}' && w[1] == b'}')
        .map(|i| start + i)
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
    extract_vars_iterative(content.as_bytes())
}

/// State for the variable extraction cursor.
struct VarCursorState<'a> {
    bytes: &'a [u8],
    pos: usize,
    line: usize,
}

/// Advance the variable extraction cursor by one logical step, returning the
/// `VariableInfo` found at this step (if any) and the updated cursor state.
///
/// Returns `None` when the cursor has reached the end of the input.
fn advance_var_cursor(
    state: VarCursorState<'_>,
) -> Option<(Option<VariableInfo>, VarCursorState<'_>)> {
    let VarCursorState { bytes, pos, line } = state;

    if pos >= bytes.len().saturating_sub(1) {
        return None;
    }

    // Newline — advance line counter
    if bytes[pos] == b'\n' {
        return Some((
            None,
            VarCursorState {
                bytes,
                pos: pos + 1,
                line: line + 1,
            },
        ));
    }

    // Comment block — skip over it
    if pos + 1 < bytes.len() && bytes[pos] == b'{' && bytes[pos + 1] == b'#' {
        return find_comment_end(bytes, pos).map(|next| {
            (
                None,
                VarCursorState {
                    bytes,
                    pos: next,
                    line,
                },
            )
        });
    }

    // Variable placeholder `{{ ... }}`
    if bytes[pos] == b'{' && pos + 1 < bytes.len() && bytes[pos + 1] == b'{' {
        if let Some(var_end) = find_variable_end(bytes, pos + 2) {
            let info = std::str::from_utf8(&bytes[pos + 2..var_end])
                .ok()
                .and_then(|raw_spec| parse_variable_spec_impl(raw_spec))
                .map(|(var_name, default_value)| VariableInfo {
                    name: var_name.to_string(),
                    line,
                    has_default: default_value.is_some(),
                    default_value,
                    placeholder: bytes[pos + 2..var_end]
                        .iter()
                        .copied()
                        .map(|b| b as char)
                        .collect::<String>()
                        .trim()
                        .to_string(),
                });
            return Some((
                info,
                VarCursorState {
                    bytes,
                    pos: var_end + 2,
                    line,
                },
            ));
        }
    }

    // Plain byte — step forward
    Some((
        None,
        VarCursorState {
            bytes,
            pos: pos + 1,
            line,
        },
    ))
}

/// Iterative (cursor-based) version of variable extraction to avoid stack
/// overflow on large templates.  Implemented as `std::iter::successors` over a
/// `VarCursorState` so that no `mut` bindings or imperative loops are needed.
fn extract_vars_iterative(bytes: &[u8]) -> Vec<VariableInfo> {
    let initial = VarCursorState {
        bytes,
        pos: 0,
        line: 0,
    };
    std::iter::successors(Some((None::<VariableInfo>, initial)), |(_, state)| {
        advance_var_cursor(VarCursorState {
            bytes: state.bytes,
            pos: state.pos,
            line: state.line,
        })
    })
    .filter_map(|(info, _)| info)
    .collect()
}

pub fn extract_partials_impl(content: &str) -> Vec<String> {
    extract_partials_iterative(content.as_bytes(), content)
}

/// State for the partial extraction cursor.
struct PartialCursorState<'a> {
    bytes: &'a [u8],
    content: &'a str,
    pos: usize,
}

/// Advance the partial extraction cursor by one logical step, returning the
/// partial name found at this step (if any) and the updated cursor state.
///
/// Returns `None` when the cursor has reached the end of the input.
fn advance_partial_cursor(
    state: PartialCursorState<'_>,
) -> Option<(Option<String>, PartialCursorState<'_>)> {
    let PartialCursorState {
        bytes,
        content,
        pos,
    } = state;

    if pos >= bytes.len().saturating_sub(2) {
        return None;
    }

    // Comment block — skip over it
    if pos + 1 < bytes.len() && bytes[pos] == b'{' && bytes[pos + 1] == b'#' {
        return skip_comment_partial(bytes, pos).map(|next| {
            (
                None,
                PartialCursorState {
                    bytes,
                    content,
                    pos: next,
                },
            )
        });
    }

    // Possible partial `{{ > name }}`
    if bytes[pos] == b'{' && bytes[pos + 1] == b'{' && pos + 2 < bytes.len() {
        let after_braces = pos + 2;
        let after_ws = after_braces
            + bytes[after_braces..]
                .iter()
                .take_while(|&&b| b == b' ' || b == b'\t')
                .count();

        if after_ws < bytes.len() && bytes[after_ws] == b'>' {
            let after_gt = after_ws + 1;
            let name_start = after_gt
                + bytes[after_gt..]
                    .iter()
                    .take_while(|&&b| b == b' ' || b == b'\t')
                    .count();
            let name_end = name_start
                + bytes[name_start..]
                    .iter()
                    .take_while(|&&b| b != b'}')
                    .count();

            if name_end < bytes.len()
                && bytes[name_end] == b'}'
                && name_end + 1 < bytes.len()
                && bytes[name_end + 1] == b'}'
            {
                let name = content[name_start..name_end].trim();
                let partial = if name.is_empty() {
                    None
                } else {
                    Some(name.to_string())
                };
                return Some((
                    partial,
                    PartialCursorState {
                        bytes,
                        content,
                        pos: name_end + 2,
                    },
                ));
            }
        }
    }

    // Plain byte — step forward
    Some((
        None,
        PartialCursorState {
            bytes,
            content,
            pos: pos + 1,
        },
    ))
}

/// Iterative (cursor-based) version of partial extraction to avoid stack
/// overflow on large templates.  Implemented as `std::iter::successors` over a
/// `PartialCursorState` so that no `mut` bindings or imperative loops are needed.
fn extract_partials_iterative(bytes: &[u8], content: &str) -> Vec<String> {
    let initial = PartialCursorState {
        bytes,
        content,
        pos: 0,
    };
    std::iter::successors(Some((None::<String>, initial)), |(_, state)| {
        advance_partial_cursor(PartialCursorState {
            bytes: state.bytes,
            content: state.content,
            pos: state.pos,
        })
    })
    .filter_map(|(partial, _)| partial)
    .collect()
}

#[derive(Debug, Clone)]
pub enum ValidationError {
    UnclosedComment { line: usize },
    UnclosedConditional { line: usize },
    UnclosedLoop { line: usize },
    InvalidConditional { line: usize, syntax: String },
    InvalidLoop { line: usize, syntax: String },
}

#[derive(Default)]
pub struct ValidationState {
    pub errors: Vec<ValidationError>,
    pub conditional_stack: Vec<(usize, &'static str)>,
    pub loop_stack: Vec<(usize, &'static str)>,
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
    state: ValidationState,
    i: usize,
    _byte: u8,
) -> ValidationState {
    if i >= bytes.len() {
        let state = match state.conditional_stack.first() {
            Some((line, _)) => ValidationState {
                errors: state
                    .errors
                    .into_iter()
                    .chain(std::iter::once(ValidationError::UnclosedConditional {
                        line: *line,
                    }))
                    .collect(),
                conditional_stack: state.conditional_stack,
                loop_stack: state.loop_stack,
            },
            None => state,
        };
        return match state.loop_stack.first() {
            Some((line, _)) => ValidationState {
                errors: state
                    .errors
                    .into_iter()
                    .chain(std::iter::once(ValidationError::UnclosedLoop {
                        line: *line,
                    }))
                    .collect(),
                conditional_stack: state.conditional_stack,
                loop_stack: state.loop_stack,
            },
            None => state,
        };
    }

    if bytes[i] == b'\n' {
        return process_byte(content, bytes, state, i + 1, bytes[i]);
    }

    if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
        return match skip_comment(bytes, i) {
            Some(next) => process_byte(content, bytes, state, next, bytes[next]),
            None => ValidationState {
                errors: state
                    .errors
                    .into_iter()
                    .chain(std::iter::once(ValidationError::UnclosedComment {
                        line: 0,
                    }))
                    .collect(),
                conditional_stack: state.conditional_stack,
                loop_stack: state.loop_stack,
            },
        };
    }

    if i + 5 < bytes.len()
        && bytes[i] == b'{'
        && bytes[i + 1] == b'%'
        && bytes[i + 2] == b' '
        && bytes[i + 3] == b'i'
        && bytes[i + 4] == b'f'
        && bytes[i + 5] == b' '
    {
        return match find_tag_end(bytes, i + 6) {
            Some(cond_end) => {
                let condition = &content[i + 6..cond_end].trim();
                let errors =
                    if condition.is_empty() || condition.contains('{') || condition.contains('}') {
                        state
                            .errors
                            .into_iter()
                            .chain(std::iter::once(ValidationError::InvalidConditional {
                                line: 0,
                                syntax: condition.to_string(),
                            }))
                            .collect()
                    } else {
                        state.errors
                    };
                let conditional_stack = state
                    .conditional_stack
                    .into_iter()
                    .chain(std::iter::once((0usize, "if")))
                    .collect();
                let next_state = ValidationState {
                    errors,
                    conditional_stack,
                    loop_stack: state.loop_stack,
                };
                process_byte(
                    content,
                    bytes,
                    next_state,
                    cond_end + 2,
                    bytes[cond_end + 2],
                )
            }
            None => ValidationState {
                errors: state
                    .errors
                    .into_iter()
                    .chain(std::iter::once(ValidationError::UnclosedConditional {
                        line: 0,
                    }))
                    .collect(),
                conditional_stack: state.conditional_stack,
                loop_stack: state.loop_stack,
            },
        };
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
        let conditional_stack: Vec<_> = state
            .conditional_stack
            .into_iter()
            .rev()
            .skip(1)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        let next_state = ValidationState {
            errors: state.errors,
            conditional_stack,
            loop_stack: state.loop_stack,
        };
        return process_byte(content, bytes, next_state, i + 11, bytes[i + 11]);
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
        return match find_tag_end(bytes, i + 7) {
            Some(header_end) => {
                let condition = &content[i + 7..header_end].trim();
                let errors = if !condition.contains(" in ") || condition.split(" in ").count() != 2
                {
                    state
                        .errors
                        .into_iter()
                        .chain(std::iter::once(ValidationError::InvalidLoop {
                            line: 0,
                            syntax: condition.to_string(),
                        }))
                        .collect()
                } else {
                    state.errors
                };
                let loop_stack = state
                    .loop_stack
                    .into_iter()
                    .chain(std::iter::once((0usize, "for")))
                    .collect();
                let next_state = ValidationState {
                    errors,
                    conditional_stack: state.conditional_stack,
                    loop_stack,
                };
                process_byte(
                    content,
                    bytes,
                    next_state,
                    header_end + 2,
                    bytes[header_end + 2],
                )
            }
            None => ValidationState {
                errors: state
                    .errors
                    .into_iter()
                    .chain(std::iter::once(ValidationError::UnclosedLoop { line: 0 }))
                    .collect(),
                conditional_stack: state.conditional_stack,
                loop_stack: state.loop_stack,
            },
        };
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
        let loop_stack: Vec<_> = state
            .loop_stack
            .into_iter()
            .rev()
            .skip(1)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect();
        let next_state = ValidationState {
            errors: state.errors,
            conditional_stack: state.conditional_stack,
            loop_stack,
        };
        return process_byte(content, bytes, next_state, i + 12, bytes[i + 12]);
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

// ============================================================================
// Comment stripping — pure domain helper
// ============================================================================

/// Determine the end-of-comment position in `bytes` starting from `pos` (the
/// `{` of `{#`), returning the byte index immediately after the closing `#}`.
///
/// Returns `None` if no `#}` is found (unclosed comment).
fn find_strip_comment_end(bytes: &[u8], pos: usize) -> Option<usize> {
    bytes[pos + 2..]
        .windows(2)
        .position(|w| w[0] == b'#' && w[1] == b'}')
        .map(|i| pos + 2 + i + 2)
}

/// Advance one step of the comment-stripping scan.
///
/// Returns `(bytes_to_emit, next_pos)`:
/// - `bytes_to_emit` is the slice from `bytes` to append to the output at this
///   step (empty when a comment is consumed).
/// - `next_pos` is the position to resume from on the next call.
///
/// `None` signals that scanning is complete.
fn strip_advance(bytes: &[u8], pos: usize) -> Option<(&[u8], usize)> {
    if pos >= bytes.len() {
        return None;
    }
    if pos + 1 < bytes.len() && bytes[pos] == b'{' && bytes[pos + 1] == b'#' {
        match find_strip_comment_end(bytes, pos) {
            Some(end) => {
                // Consume one optional trailing newline to avoid blank lines.
                let next = if end < bytes.len() && bytes[end] == b'\n' {
                    end + 1
                } else {
                    end
                };
                // Emit nothing; resume after the comment (and optional newline).
                Some((&bytes[pos..pos], next))
            }
            // Unclosed comment: pass through the opening `{` byte so the
            // unclosed marker is preserved verbatim.
            None => Some((&bytes[pos..pos + 1], pos + 1)),
        }
    } else {
        Some((&bytes[pos..pos + 1], pos + 1))
    }
}

/// Strip all `{# ... #}` comment blocks from a template string.
///
/// Scans the input byte-by-byte using a cursor driven by
/// [`std::iter::successors`]. When `{#` is found, advances past the matching
/// `#}` (and one trailing `\n` if present, to avoid leaving blank lines).
/// Bytes outside comments are copied verbatim to the output buffer.
///
/// Unclosed `{#` markers (no matching `#}`) are passed through unchanged;
/// validation in `validate_template_bytes` surfaces those as errors.
///
/// # Safety
/// All emitted bytes are subslices of the original `&str` input, which is
/// guaranteed valid UTF-8. `{#` and `#}` are pure ASCII, so the scan never
/// splits a multi-byte UTF-8 sequence. The `from_utf8` call therefore always
/// succeeds; `unwrap_or_else` is a defensive fallback returning the original
/// content.
pub(crate) fn strip_comments_impl(content: &str) -> String {
    let bytes = content.as_bytes();
    let output: Vec<u8> = std::iter::successors(Some((b"".as_slice(), 0usize)), |&(_, pos)| {
        strip_advance(bytes, pos)
    })
    .flat_map(|(slice, _)| slice.iter().copied())
    .collect();
    // SAFETY: see doc comment above.
    String::from_utf8(output).unwrap_or_else(|_| content.to_string())
}

#[cfg(test)]
mod proptest_parsers {
    use super::{
        extract_partials_impl, extract_variables_impl, parse_loop_header_impl,
        parse_metadata_line_impl, parse_variable_spec_impl,
    };
    use proptest::prelude::*;

    proptest! {
        /// `parse_variable_spec_impl` must never panic on any string input.
        #[test]
        fn parse_variable_spec_impl_never_panics(s in ".*") {
            let _ = parse_variable_spec_impl(&s);
        }

        /// `parse_metadata_line_impl` must never panic on any string input,
        /// including strings shorter than 4 bytes or with multibyte boundaries.
        #[test]
        fn parse_metadata_line_impl_never_panics(s in ".*") {
            let _ = parse_metadata_line_impl(&s);
        }

        /// `parse_metadata_line_impl` on a well-formed `{# Version: X #}` line
        /// always extracts the version.
        #[test]
        fn parse_metadata_line_impl_extracts_version(v in "[A-Za-z0-9._-]{1,20}") {
            let line = format!("{{# Version: {v} #}}");
            let result = parse_metadata_line_impl(&line);
            prop_assert!(result.is_some());
            let (version, _purpose) = result.unwrap();
            prop_assert_eq!(version, Some(v));
        }

        /// `parse_loop_header_impl` must never panic on any string input.
        #[test]
        fn parse_loop_header_impl_never_panics(s in ".*") {
            let _ = parse_loop_header_impl(&s);
        }

        /// `extract_variables_impl` must never panic on any string input.
        #[test]
        fn extract_variables_impl_never_panics(s in ".*") {
            let _ = extract_variables_impl(&s);
        }

        /// `extract_partials_impl` must never panic on any string input.
        #[test]
        fn extract_partials_impl_never_panics(s in ".*") {
            let _ = extract_partials_impl(&s);
        }

        /// A string with no `{{` markers produces zero variables.
        #[test]
        fn extract_variables_impl_empty_on_no_braces(s in "[^{]*") {
            let vars = extract_variables_impl(&s);
            prop_assert!(vars.is_empty());
        }

        /// `extract_partials_impl` output contains only non-empty names.
        #[test]
        fn extract_partials_impl_names_are_nonempty(s in ".*") {
            let partials = extract_partials_impl(&s);
            for name in &partials {
                prop_assert!(!name.is_empty());
            }
        }
    }
}
