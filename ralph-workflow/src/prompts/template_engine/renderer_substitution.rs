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
        _empty_is_missing: bool,
    ) -> (String, Vec<crate::prompts::SubstitutionEntry>, Vec<String>) {
        use crate::prompts::{SubstitutionEntry, SubstitutionSource};

        let bytes = content.as_bytes();

        let replacements: Vec<(usize, usize, String)> = bytes
            .windows(2)
            .enumerate()
            .filter_map(|(pos, window)| {
                if window[0] == b'{' && window[1] == b'{' {
                    let start = pos;
                    let rest = &bytes[pos + 2..];

                    // Skip whitespace after {{
                    let ws_end = rest
                        .iter()
                        .position(|&b| b != b' ' && b != b'\t')
                        .unwrap_or(rest.len());
                    let name_start = pos + 2 + ws_end;
                    let rest = &rest[ws_end..];

                    // Find closing }}
                    let close_pos = rest.windows(2).position(|w| w[0] == b'}' && w[1] == b'}');

                    // If no closing }} found, return None
                    let end_offset = close_pos?;

                    let end = name_start + end_offset + 2;
                    let var_spec = &content[name_start..end - 2];

                    if var_spec.trim().starts_with('>') || var_spec.trim().is_empty() {
                        return None;
                    }

                    let trimmed_var = var_spec.trim();

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

                    let (replacement, should_replace) = if let Some(value) = variables.get(var_name)
                    {
                        if !value.is_empty() {
                            (value.clone(), true)
                        } else if let Some(default) = &default_value {
                            (default.clone(), true)
                        } else {
                            (String::new(), false)
                        }
                    } else if let Some(default) = &default_value {
                        (default.clone(), true)
                    } else {
                        (String::new(), false)
                    };

                    if should_replace {
                        Some((start, end, replacement))
                    } else {
                        None
                    }
                } else {
                    None
                }
            })
            .collect();

        // Now process substituted and unsubstituted
        let substituted: Vec<SubstitutionEntry> = replacements
            .iter()
            .filter_map(|(start, end, _replacement)| {
                let var_spec = &content[*start + 2..*end - 2];
                let trimmed_var = var_spec.trim();
                if trimmed_var.starts_with('>') || trimmed_var.is_empty() {
                    return None;
                }

                let var_name = trimmed_var.split('|').next().unwrap_or(trimmed_var).trim();

                if let Some(value) = variables.get(var_name) {
                    if !value.is_empty() {
                        Some(SubstitutionEntry {
                            name: var_name.to_string(),
                            source: SubstitutionSource::Value,
                        })
                    } else if trimmed_var.contains('|') {
                        Some(SubstitutionEntry {
                            name: var_name.to_string(),
                            source: SubstitutionSource::EmptyWithDefault,
                        })
                    } else {
                        None
                    }
                } else if trimmed_var.contains('|') {
                    Some(SubstitutionEntry {
                        name: var_name.to_string(),
                        source: SubstitutionSource::Default,
                    })
                } else {
                    None
                }
            })
            .collect();

        let unsubstituted: Vec<String> = content
            .split("{{")
            .skip(1)
            .filter_map(|part| {
                let end = part.find("}}")?;
                let var_spec = &part[..end];
                let trimmed = var_spec.trim();
                if trimmed.starts_with('>') || trimmed.is_empty() {
                    return None;
                }
                let var_name = trimmed.split('|').next().unwrap_or(trimmed).trim();
                if variables.get(var_name).is_none() && !trimmed.contains('|') {
                    Some(var_name.to_string())
                } else {
                    None
                }
            })
            .collect();

        // Build result - sort by position descending and fold to apply replacements
        let mut sorted_replacements: Vec<_> = replacements.iter().collect();
        sorted_replacements.sort_by_key(|(start, _, _)| *start);

        let result = sorted_replacements.into_iter().rev().fold(
            content.to_string(),
            |acc, (start, end, replacement)| {
                format!("{}{}{}", &acc[..*start], replacement, &acc[*end..])
            },
        );

        (result, substituted, unsubstituted)
    }

    fn next_literal_token(
        result: &str,
        content: &str,
        literal_segments: &[LiteralSegment],
    ) -> String {
        let start_index = literal_segments.len();
        (start_index..)
            .map(|index| format!("__RALPH_TEMPLATE_LITERAL_{index}__"))
            .find(|token| !result.contains(token) && !content.contains(token))
            .expect("Should always find a unique token")
    }

    fn restore_literal_segments(content: String, literal_segments: &[LiteralSegment]) -> String {
        literal_segments.iter().rev().fold(content, |acc, segment| {
            acc.replace(&segment.token, &segment.content)
        })
    }
}
