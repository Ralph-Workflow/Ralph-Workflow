// Template variable substitution and literal segment helpers.

impl Template {
    /// Substitute variables in content (simple version without partials or conditionals).
    /// Returns `(result, substituted, unsubstituted)` where:
    /// - `substituted` is a list of `SubstitutionEntry` tracking how each var was resolved
    /// - `unsubstituted` is a list of variable names that had no value AND no default
    fn substitute_variables(
        content: &str,
        variables: &HashMap<&str, String>,
    ) -> (String, Vec<crate::prompts::SubstitutionEntry>, Vec<String>) {
        Self::substitute_variables_with_empty_policy(content, variables, true)
    }

    /// Substitute variables while allowing empty values without marking them missing.
    fn substitute_variables_allow_empty(
        content: &str,
        variables: &HashMap<&str, String>,
    ) -> (String, Vec<crate::prompts::SubstitutionEntry>, Vec<String>) {
        Self::substitute_variables_with_empty_policy(content, variables, false)
    }

    fn substitute_variables_with_empty_policy(
        content: &str,
        variables: &HashMap<&str, String>,
        empty_is_missing: bool,
    ) -> (String, Vec<crate::prompts::SubstitutionEntry>, Vec<String>) {
        use crate::prompts::{SubstitutionEntry, SubstitutionSource};

        let mut result = content.to_string();
        let mut substituted = Vec::new();
        let mut unsubstituted = Vec::new();

        // Find all {{...}} patterns
        let mut replacements = Vec::new();
        let mut i = 0;
        let bytes = content.as_bytes();
        while i < bytes.len().saturating_sub(1) {
            if bytes[i] == b'{' && i + 1 < bytes.len() && bytes[i + 1] == b'{' {
                let start = i;
                i = i.saturating_add(2);

                // Skip whitespace after {{
                while i < bytes.len() && (bytes[i] == b' ' || bytes[i] == b'\t') {
                    i = i.saturating_add(1);
                }

                let name_start = i;

                // Find the closing }}
                while i < bytes.len()
                    && !(bytes[i] == b'}' && i + 1 < bytes.len() && bytes[i + 1] == b'}')
                {
                    i = i.saturating_add(1);
                }

                if i < bytes.len()
                    && bytes[i] == b'}'
                    && i + 1 < bytes.len()
                    && bytes[i + 1] == b'}'
                {
                    let end = i + 2;
                    let var_spec = &content[name_start..i];

                    // Check for partial reference {{> partial}} - skip it
                    if var_spec.trim().starts_with('>') {
                        i = end;
                        continue;
                    }

                    // Skip if variable name is empty or whitespace only
                    let trimmed_var = var_spec.trim();
                    if trimmed_var.is_empty() {
                        i = end;
                        continue;
                    }

                    // Check for default value syntax: {{VAR|default="value"}}
                    let (var_name, default_value) =
                        var_spec.find('|').map_or((trimmed_var, None), |pipe_pos| {
                            let name = var_spec[..pipe_pos].trim();
                            let rest = &var_spec[pipe_pos + 1..];
                            // Parse default="value"
                            rest.find('=').map_or((name, None), |eq_pos| {
                                let key = rest[..eq_pos].trim();
                                if key == "default" {
                                    let value = rest[eq_pos + 1..].trim();
                                    // Remove quotes if present (both single and double)
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

                    // Look up the variable and track how it was resolved
                    let (replacement, should_replace, source) =
                        if let Some(value) = variables.get(var_name) {
                            if !value.is_empty() {
                                // Value provided and non-empty
                                (value.clone(), true, Some(SubstitutionSource::Value))
                            } else if let Some(default) = &default_value {
                                // Value provided but empty, use default
                                (
                                    default.clone(),
                                    true,
                                    Some(SubstitutionSource::EmptyWithDefault),
                                )
                            } else {
                                // Variable exists but is empty, and no default - treat as missing if configured
                                if empty_is_missing {
                                    unsubstituted.push(var_name.to_string());
                                }
                                (String::new(), false, None)
                            }
                        } else if let Some(default) = &default_value {
                            (default.clone(), true, Some(SubstitutionSource::Default))
                        } else {
                            // No value AND no default - truly unsubstituted
                            unsubstituted.push(var_name.to_string());
                            (String::new(), false, None)
                        };

                    if should_replace {
                        if let Some(src) = source {
                            substituted.push(SubstitutionEntry {
                                name: var_name.to_string(),
                                source: src,
                            });
                        }
                        replacements.push((start, end, replacement));
                    }
                    i = end;
                    continue;
                }
            }
            i = i.saturating_add(1);
        }

        // Apply replacements in reverse order to maintain correct positions
        for (start, end, replacement) in replacements.into_iter().rev() {
            result.replace_range(start..end, &replacement);
        }

        (result, substituted, unsubstituted)
    }

    fn next_literal_token(
        result: &str,
        content: &str,
        literal_segments: &[LiteralSegment],
    ) -> String {
        let mut index = literal_segments.len();
        loop {
            let token = format!("__RALPH_TEMPLATE_LITERAL_{index}__");
            if !result.contains(&token) && !content.contains(&token) {
                return token;
            }
            index = index.saturating_add(1);
        }
    }

    fn restore_literal_segments(
        mut content: String,
        literal_segments: &[LiteralSegment],
    ) -> String {
        for segment in literal_segments.iter().rev() {
            content = content.replace(&segment.token, &segment.content);
        }
        content
    }
}
