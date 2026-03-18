//! Template parsing runtime - imperative byte-by-byte parsing.
//!
//! This code is inherently imperative and belongs in a boundary module.

use crate::prompts::template_validator::{TemplateMetadata, VariableInfo};

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
