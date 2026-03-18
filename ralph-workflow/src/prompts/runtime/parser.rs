//! Template parsing runtime - byte-by-byte parsing that belongs in boundary code.

use crate::prompts::template_validator::{TemplateMetadata, VariableInfo};

/// Extract all variable references from template content.
///
/// Returns a list of all `{{VARIABLE}}` references found in the template,
/// including their line numbers and default values if present.
#[must_use]
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
pub fn extract_variables(content: &str) -> Vec<VariableInfo> {
    let bytes = content.as_bytes();
    let mut variables = Vec::new();
    let mut i = 0;
    let mut line = 0;

    while i < bytes.len().saturating_sub(1) {
        if bytes[i] == b'\n' {
            line += 1;
        }

        if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
            i += 2;
            while i + 1 < bytes.len() && !(bytes[i] == b'#' && bytes[i + 1] == b'}') {
                if bytes[i] == b'\n' {
                    line += 1;
                }
                i += 1;
            }
            if i + 1 < bytes.len() {
                i += 2;
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
                let trimmed_var = var_spec.trim();

                if !trimmed_var.starts_with('>') && !trimmed_var.is_empty() {
                    let (var_name, default_value) =
                        var_spec.find('|').map_or((trimmed_var, None), |pipe_pos| {
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

/// Extract all partial references from template content.
#[must_use]
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
pub fn extract_partials(content: &str) -> Vec<String> {
    let bytes = content.as_bytes();
    let mut partials = Vec::new();
    let mut i = 0;

    while i < bytes.len().saturating_sub(2) {
        if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
            i += 2;
            while i + 1 < bytes.len() && !(bytes[i] == b'#' && bytes[i + 1] == b'}') {
                i += 1;
            }
            if i + 1 < bytes.len() {
                i += 2;
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

        let inner = line[2..line.len() - 2].trim();

        if let Some(rest) = inner.strip_prefix("Version:") {
            version = Some(rest.trim().to_string());
        } else if let Some(rest) = inner.strip_prefix("PURPOSE:") {
            purpose = Some(rest.trim().to_string());
        }
    }

    TemplateMetadata { version, purpose }
}
