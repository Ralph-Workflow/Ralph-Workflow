//! Template rendering runtime - imperative rendering logic that belongs in boundary code.

use std::collections::HashMap;

use crate::prompts::template_registry::TemplateError;
use crate::prompts::template_validator::{SubstitutionEntry, SubstitutionLog};
use crate::prompts::RenderedTemplate;

use super::parser::{extract_partials, extract_variables};
use super::Template;

struct LiteralSegment {
    token: String,
    content: String,
}

struct LoopRenderLog {
    token: String,
    substituted: Vec<SubstitutionEntry>,
    unsubstituted: Vec<String>,
}

impl Template {
    /// Render the template with the provided variables.
    pub fn render(&self, variables: &HashMap<&str, String>) -> Result<String, TemplateError> {
        let mut literal_segments = Vec::new();
        let (result, loop_logs) =
            Self::process_loops_with_log(&self.content, variables, &mut literal_segments);

        let result = Self::process_conditionals(&result, variables);

        let (result_after_sub, _substituted, unsubstituted) =
            Self::substitute_variables_allow_empty(&result, variables);

        let missing: Vec<String> = loop_logs
            .iter()
            .filter(|loop_log| result.contains(&loop_log.token))
            .flat_map(|loop_log| loop_log.unsubstituted.clone())
            .chain(unsubstituted)
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();

        if let Some(first_missing) = missing.first() {
            return Err(TemplateError::MissingVariable(first_missing.clone()));
        }

        Ok(Self::restore_literal_segments(
            result_after_sub,
            &literal_segments,
        ))
    }

    /// Render the template with variables and partials support.
    pub fn render_with_partials(
        &self,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
    ) -> Result<String, TemplateError> {
        self.render_with_partials_recursive(variables, partials, &mut Vec::new())
    }

    /// Render the template with variables and partials, returning substitution log.
    pub fn render_with_log(
        &self,
        template_name: &str,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
    ) -> Result<RenderedTemplate, TemplateError> {
        self.render_with_log_recursive(template_name, variables, partials, &mut Vec::new())
    }

    fn render_with_partials_recursive(
        &self,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
        visited: &mut Vec<String>,
    ) -> Result<String, TemplateError> {
        let mut literal_segments = Vec::new();
        let mut result = self.content.clone();

        let partial_refs = extract_partials(&result);

        for (full_match, partial_name) in partial_refs.into_iter().rev() {
            if visited.contains(&partial_name) {
                let mut chain = visited.clone();
                chain.push(partial_name);
                return Err(TemplateError::CircularReference(chain));
            }

            let partial_content = partials
                .get(&partial_name)
                .ok_or_else(|| TemplateError::PartialNotFound(partial_name.clone()))?;

            let partial_template = Self::new(partial_content);
            visited.push(partial_name.clone());
            let rendered_partial =
                partial_template.render_with_partials_recursive(variables, partials, visited)?;
            visited.pop();

            let token = Self::next_literal_token(&result, &rendered_partial, &literal_segments);
            literal_segments.push(LiteralSegment {
                token: token.clone(),
                content: rendered_partial,
            });
            result = result.replace(&full_match, &token);
        }

        let (loop_processed, loop_logs) =
            Self::process_loops_with_log(&result, variables, &mut literal_segments);
        let result = loop_processed;

        let result = Self::process_conditionals(&result, variables);

        let (result_after_sub, _substituted, unsubstituted) =
            Self::substitute_variables_allow_empty(&result, variables);

        let missing: Vec<String> = loop_logs
            .iter()
            .filter(|loop_log| result.contains(&loop_log.token))
            .flat_map(|loop_log| loop_log.unsubstituted.clone())
            .chain(unsubstituted)
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();

        if let Some(first_missing) = missing.first() {
            return Err(TemplateError::MissingVariable(first_missing.clone()));
        }

        Ok(Self::restore_literal_segments(
            result_after_sub,
            &literal_segments,
        ))
    }

    fn render_with_log_recursive(
        &self,
        template_name: &str,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
        visited: &mut Vec<String>,
    ) -> Result<RenderedTemplate, TemplateError> {
        let mut log = SubstitutionLog {
            template_name: template_name.to_string(),
            substituted: Vec::new(),
            unsubstituted: Vec::new(),
        };
        let mut literal_segments = Vec::new();

        let mut result = self.content.clone();
        let partial_refs = extract_partials(&result);

        for (full_match, partial_name) in partial_refs.into_iter().rev() {
            if visited.contains(&partial_name) {
                let mut chain = visited.clone();
                chain.push(partial_name);
                return Err(TemplateError::CircularReference(chain));
            }

            let partial_content = partials
                .get(&partial_name)
                .ok_or_else(|| TemplateError::PartialNotFound(partial_name.clone()))?;

            let partial_template = Self::new(partial_content);
            visited.push(partial_name.clone());
            let rendered_partial = partial_template.render_with_log_recursive(
                template_name,
                variables,
                partials,
                visited,
            )?;
            visited.pop();

            let token =
                Self::next_literal_token(&result, &rendered_partial.content, &literal_segments);
            literal_segments.push(LiteralSegment {
                token: token.clone(),
                content: rendered_partial.content,
            });
            result = result.replace(&full_match, &token);
            log.substituted.extend(rendered_partial.log.substituted);
            let new_unsub: Vec<String> = rendered_partial
                .log
                .unsubstituted
                .into_iter()
                .filter(|name| !log.unsubstituted.contains(name))
                .collect();
            log.unsubstituted.extend(new_unsub);
        }

        let (loop_processed, loop_logs) =
            Self::process_loops_with_log(&result, variables, &mut literal_segments);
        let result = loop_processed;

        let result = Self::process_conditionals(&result, variables);

        for loop_log in loop_logs {
            if result.contains(&loop_log.token) {
                log.substituted.extend(loop_log.substituted);
                let new_unsub: Vec<String> = loop_log
                    .unsubstituted
                    .into_iter()
                    .filter(|name| !log.unsubstituted.contains(name))
                    .collect();
                log.unsubstituted.extend(new_unsub);
            }
        }

        let (result_after_sub, substituted, unsubstituted) =
            Self::substitute_variables(&result, variables);

        log.substituted.extend(substituted);
        let new_unsub: Vec<String> = unsubstituted
            .into_iter()
            .filter(|name| !log.unsubstituted.contains(name))
            .collect();
        log.unsubstituted.extend(new_unsub);

        Ok(RenderedTemplate {
            content: Self::restore_literal_segments(result_after_sub, &literal_segments),
            log,
        })
    }

    fn process_loops_with_log(
        content: &str,
        variables: &HashMap<&str, String>,
        literal_segments: &mut Vec<LiteralSegment>,
    ) -> (String, Vec<LoopRenderLog>) {
        let mut result = content.to_string();
        let mut loop_logs = Vec::new();
        let mut token_counter = 0;

        while let Some(for_start) = result.find("{% for ") {
            if let Some(for_end) = result[for_start..].find("%}") {
                let for_end = for_start + for_end;
                let full_match = &result[for_start..for_end + 2];

                if let Some(in_pos) = full_match.find(" in ") {
                    let header = &full_match[7..in_pos];
                    let in_pos = in_pos - 7;
                    let body_start = full_match.find("%}").map_or(full_match.len(), |p| p + 2);
                    let body = &full_match[body_start..full_match.len() - 2];

                    if let Some(var_name) = header.trim().split_whitespace().next() {
                        if let Some(values) = variables.get(var_name) {
                            let items: Vec<&str> = if values.contains(',') {
                                values.split(',').map(str::trim).collect()
                            } else {
                                values
                                    .lines()
                                    .map(str::trim)
                                    .filter(|s| !s.is_empty())
                                    .collect()
                            };

                            let mut rendered_items = Vec::new();
                            let mut unsubstituted = Vec::new();

                            for item in items {
                                let mut item_content = body.to_string();
                                for (key, val) in variables {
                                    item_content =
                                        item_content.replace(&format!("{{{{{}}}}}", key), val);
                                }
                                item_content =
                                    item_content.replace(&format!("{{{}}}", var_name), item);

                                let vars_in_item: Vec<&str> = extract_variables(&item_content)
                                    .iter()
                                    .map(|v| v.name.as_str())
                                    .collect();

                                for var in vars_in_item {
                                    if !variables.contains_key(var)
                                        && !item_content.contains(&format!("{{{}}}", var))
                                    {
                                        unsubstituted.push(var.to_string());
                                    }
                                }

                                rendered_items.push(item_content);
                            }

                            let loop_token = format!("__LOOP_TOKEN_{}__", token_counter);
                            token_counter += 1;

                            literal_segments.push(LiteralSegment {
                                token: loop_token.clone(),
                                content: rendered_items.join("\n"),
                            });

                            loop_logs.push(LoopRenderLog {
                                token: loop_token.clone(),
                                substituted: vec![SubstitutionEntry {
                                    name: var_name.to_string(),
                                    source: crate::prompts::template_validator::SubstitutionSource::Value,
                                }],
                                unsubstituted,
                            });

                            result = result.replace(full_match, &loop_token);
                        } else {
                            break;
                        }
                    } else {
                        break;
                    }
                } else {
                    break;
                }
            } else {
                break;
            }
        }

        (result, loop_logs)
    }

    fn process_conditionals(content: &str, variables: &HashMap<&str, String>) -> String {
        let mut result = content.to_string();

        while let Some(if_start) = result.find("{% if ") {
            if let Some(end_pos) = result[if_start..].find("%}") {
                let if_end = if_start + end_pos;
                let full_match = &result[if_start..if_end + 2];

                if let Some(then_pos) = full_match.find("%}") {
                    let condition = &full_match[6..then_pos];
                    let body_start = then_pos + 2;

                    let else_block = full_match.find("{% else %}");
                    let endif_block = full_match.find("{% endif %}");

                    let (condition_true, body) = if let Some(else_pos) = else_block {
                        let cond = condition.trim();
                        let is_truthy = variables.get(cond).map(|v| !v.is_empty()).unwrap_or(false);
                        (is_truthy, &full_match[body_start..else_pos])
                    } else if let Some(endif_pos) = endif_block {
                        let cond = condition.trim();
                        let is_truthy = variables.get(cond).map(|v| !v.is_empty()).unwrap_or(false);
                        (is_truthy, &full_match[body_start..endif_pos])
                    } else {
                        break;
                    };

                    let replacement = if condition_true { body.trim() } else { "" };
                    result = result.replace(full_match, replacement);
                } else {
                    break;
                }
            } else {
                break;
            }
        }

        while let Some(else_pos) = result.find("{% else %}") {
            if let Some(endif_pos) = result[else_pos..].find("{% endif %}") {
                let endif_pos = else_pos + endif_pos;
                let full_match = &result[else_pos..endif_pos + 11];
                result = result.replace(full_match, "");
            } else {
                break;
            }
        }

        while let Some(endif_pos) = result.find("{% endif %}") {
            result = result.replace("{% endif %}", "");
        }

        result
    }

    fn substitute_variables_allow_empty(
        content: &str,
        variables: &HashMap<&str, String>,
    ) -> (String, Vec<SubstitutionEntry>, Vec<String>) {
        let mut result = content.to_string();
        let mut substituted = Vec::new();
        let mut unsubstituted = Vec::new();

        let vars = extract_variables(&result);
        for var in vars {
            let placeholder = format!("{{{{{}}}}}", var.name);
            let placeholder_with_default = format!("{{{{{}}}}}", var.name);

            if result.contains(&placeholder_with_default) {
                if let Some(value) = variables.get(var.name.as_str()) {
                    result = result.replace(&placeholder_with_default, value);
                    substituted.push(SubstitutionEntry {
                        name: var.name.clone(),
                        source: if value.is_empty() {
                            crate::prompts::template_validator::SubstitutionSource::EmptyWithDefault
                        } else if var.has_default {
                            crate::prompts::template_validator::SubstitutionSource::Default
                        } else {
                            crate::prompts::template_validator::SubstitutionSource::Value
                        },
                    });
                } else if var.has_default {
                    let default_val = var.default_value.unwrap_or_default();
                    result = result.replace(&placeholder_with_default, &default_val);
                    substituted.push(SubstitutionEntry {
                        name: var.name.clone(),
                        source: crate::prompts::template_validator::SubstitutionSource::Default,
                    });
                } else {
                    unsubstituted.push(var.name.clone());
                }
            } else if result.contains(&placeholder) {
                if let Some(value) = variables.get(var.name.as_str()) {
                    result = result.replace(&placeholder, value);
                    substituted.push(SubstitutionEntry {
                        name: var.name.clone(),
                        source: crate::prompts::template_validator::SubstitutionSource::Value,
                    });
                } else {
                    unsubstituted.push(var.name.clone());
                }
            }
        }

        (result, substituted, unsubstituted)
    }

    fn substitute_variables(
        content: &str,
        variables: &HashMap<&str, String>,
    ) -> (String, Vec<SubstitutionEntry>, Vec<String>) {
        Self::substitute_variables_allow_empty(content, variables)
    }

    fn next_literal_token(
        content: &str,
        replacement: &str,
        literal_segments: &[LiteralSegment],
    ) -> String {
        let mut token = format!("__LITERAL_{}__", literal_segments.len());
        while content.contains(&token) || literal_segments.iter().any(|s| s.token == token) {
            token = format!(
                "__LITERAL_{}__{}",
                literal_segments.len(),
                replacement.len()
            );
        }
        token
    }

    fn restore_literal_segments(content: &str, literal_segments: &[LiteralSegment]) -> String {
        let mut result = content.to_string();
        for segment in literal_segments {
            result = result.replace(&segment.token, &segment.content);
        }
        result
    }
}
