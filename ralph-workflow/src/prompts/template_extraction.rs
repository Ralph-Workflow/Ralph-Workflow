//! Template variable and partial extraction.
//!
//! Provides functions for extracting variables, partials, and metadata
//! from template content.

use super::template_types::{TemplateMetadata, VariableInfo};

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
    let mut variables = Vec::new();
    let bytes = content.as_bytes();
    let mut i = 0;
    let mut line = 0;

    while i < bytes.len().saturating_sub(1) {
        // Track line numbers
        if bytes[i] == b'\n' {
            line += 1;
        }

        // Skip comment blocks
        if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
            // Skip to end of comment
            i += 2;
            while i + 1 < bytes.len() && !(bytes[i] == b'#' && bytes[i + 1] == b'}') {
                if bytes[i] == b'\n' {
                    line += 1;
                }
                i += 1;
            }
            if i + 1 < bytes.len() {
                i += 2; // Skip #}
            }
            continue;
        }

        // Check for {{...}} pattern
        if bytes[i] == b'{' && i + 1 < bytes.len() && bytes[i + 1] == b'{' {
            i += 2;

            // Skip whitespace after {{
            while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                i += 1;
            }

            let name_start = i;

            // Find the closing }}
            while i < bytes.len()
                && !(bytes[i] == b'}' && i + 1 < bytes.len() && bytes[i + 1] == b'}')
            {
                i += 1;
            }

            if i < bytes.len() && bytes[i] == b'}' && i + 1 < bytes.len() && bytes[i + 1] == b'}' {
                let var_spec = &content[name_start..i];
                let trimmed_var = var_spec.trim();

                // Skip partial references {{> partial}}
                if !trimmed_var.starts_with('>') && !trimmed_var.is_empty() {
                    // Check for default value syntax
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
///
/// Returns a list of all `{{> partial}}` references found in the template.
#[must_use]
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked index arithmetic"
)]
pub fn extract_partials(content: &str) -> Vec<String> {
    let mut partials = Vec::new();
    let bytes = content.as_bytes();
    let mut i = 0;

    while i < bytes.len().saturating_sub(2) {
        // Skip comment blocks
        if i + 1 < bytes.len() && bytes[i] == b'{' && bytes[i + 1] == b'#' {
            // Skip to end of comment
            i += 2;
            while i + 1 < bytes.len() && !(bytes[i] == b'#' && bytes[i + 1] == b'}') {
                i += 1;
            }
            if i + 1 < bytes.len() {
                i += 2; // Skip #}
            }
            continue;
        }

        // Check for {{> pattern
        if bytes[i] == b'{' && bytes[i + 1] == b'{' && i + 2 < bytes.len() {
            i += 2;

            // Skip whitespace after {{
            while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                i += 1;
            }

            // Check for > character
            if i < bytes.len() && bytes[i] == b'>' {
                i += 1;

                // Skip whitespace after >
                while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                    i += 1;
                }

                // Extract partial name until }}
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
///
/// Parses structured comments like:
/// ```text
/// {# Template: name #}
/// {# Version: 1.0 #}
/// {# VARIABLES: VAR1, VAR2 #}
/// ```
#[must_use]
#[expect(
    clippy::arithmetic_side_effects,
    reason = "bounds-checked string slicing"
)]
pub fn extract_metadata(content: &str) -> TemplateMetadata {
    let mut version = None;
    let mut purpose = None;

    for line in content.lines().take(50) {
        // Look for comment markers
        let line = line.trim();
        if !line.starts_with("{#") || !line.ends_with("#}") {
            continue;
        }

        let inner = line[2..line.len() - 2].trim();

        // Parse Version: x.x
        if let Some(rest) = inner.strip_prefix("Version:") {
            version = Some(rest.trim().to_string());
        } else if let Some(rest) = inner.strip_prefix("PURPOSE:") {
            // Parse PURPOSE: description
            purpose = Some(rest.trim().to_string());
        }
    }

    TemplateMetadata { version, purpose }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_simple_variable() {
        let content = "Hello {{NAME}}";
        let vars = extract_variables(content);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "NAME");
        assert!(!vars[0].has_default);
    }

    #[test]
    fn test_extract_variable_with_whitespace() {
        let content = "Value: {{ VALUE }}";
        let vars = extract_variables(content);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "VALUE");
    }

    #[test]
    fn test_extract_variable_with_default() {
        let content = "Hello {{NAME|default=\"Guest\"}}";
        let vars = extract_variables(content);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "NAME");
        assert!(vars[0].has_default);
        assert_eq!(vars[0].default_value, Some("Guest".to_string()));
    }

    #[test]
    fn test_extract_variable_with_default_single_quotes() {
        let content = "Hello {{NAME|default='Guest'}}";
        let vars = extract_variables(content);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].default_value, Some("Guest".to_string()));
    }

    #[test]
    fn test_extract_partials() {
        let content = "{{> shared/_header}}\nContent";
        let partials = extract_partials(content);
        assert_eq!(partials.len(), 1);
        assert_eq!(partials[0], "shared/_header");
    }

    #[test]
    fn test_extract_multiple_partials() {
        let content = "{{> header}}\n{{> footer}}";
        let partials = extract_partials(content);
        assert_eq!(partials.len(), 2);
        assert_eq!(partials[0], "header");
        assert_eq!(partials[1], "footer");
    }

    #[test]
    fn test_extract_metadata() {
        let content = r"{# Template: test.txt #}
{# Version: 1.0 #}
{# PURPOSE: Test template #}
{# VARIABLES: {{NAME}}, {{AGE}} #}
Content here";

        let metadata = extract_metadata(content);
        assert_eq!(metadata.version, Some("1.0".to_string()));
        assert_eq!(metadata.purpose, Some("Test template".to_string()));
    }

    #[test]
    fn test_skip_variables_in_comments() {
        let content = "{# This is a comment with {{VARIABLE}} #}\nHello {{NAME}}";
        let vars = extract_variables(content);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "NAME");
    }

    #[test]
    fn test_skip_partials_in_variable_extraction() {
        let content = "{{> partial}}\n{{NAME}}";
        let vars = extract_variables(content);
        assert_eq!(vars.len(), 1);
        assert_eq!(vars[0].name, "NAME");
    }

    #[test]
    fn test_extract_variables_from_conditional() {
        let content = "{% if NAME %}Hello {{NAME}}{% endif %}";
        let vars = extract_variables(content);
        assert_eq!(vars.len(), 1); // Only NAME in output is extracted
    }
}
