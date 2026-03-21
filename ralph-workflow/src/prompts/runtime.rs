//! Runtime boundary module for prompts.
//!
//! This module contains imperative code (template parsing, rendering) that cannot
//! be easily converted to functional style. It satisfies the dylint boundary-module
//! check.

use std::collections::HashMap;
use std::fmt;

pub use crate::prompts::io::extract_metadata;
pub use crate::prompts::io::extract_partials;
pub use crate::prompts::io::extract_variables;
pub use crate::prompts::io::validate_syntax;
pub use crate::prompts::template_registry::TemplateError;
pub use crate::prompts::template_validator::{
    RenderedTemplate, SubstitutionEntry, SubstitutionLog, SubstitutionSource,
};

/// Template for rendering prompts with variable substitution.
pub struct Template {
    pub content: String,
}

impl Template {
    #[must_use]
    pub fn new(content: &str) -> Self {
        Self {
            content: content.to_string(),
        }
    }
}

impl fmt::Debug for Template {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("Template")
            .field(
                "content",
                &self.content.chars().take(50).collect::<String>(),
            )
            .finish()
    }
}

// =========================================================================
// Template rendering runtime - imperative rendering logic.
// =========================================================================

struct LiteralSegment {
    token: String,
    content: String,
}

struct LoopRenderLog {
    token: String,
    substituted: Vec<SubstitutionEntry>,
    unsubstituted: Vec<String>,
}

fn parse_loop_header(full_match: &str) -> Option<(&str, &str)> {
    crate::prompts::template_parsing::parse_loop_header_impl(full_match)
}

fn split_loop_items(values: &str) -> Vec<&str> {
    crate::prompts::template_parsing::split_loop_items_impl(values)
}

fn render_loop_item(
    body: &str,
    item: &str,
    var_name: &str,
    variables: &HashMap<&str, String>,
) -> String {
    // First apply all variable substitutions via fold, then apply item substitution
    let after_vars = variables
        .iter()
        .fold(body.to_string(), |content, (key, val)| {
            content.replace(&format!("{{{{{}}}}}", key), val)
        });
    after_vars.replace(&format!("{{{}}}", var_name), item)
}

fn find_unsubstituted_vars(item_content: &str, variables: &HashMap<&str, String>) -> Vec<String> {
    extract_variables(item_content)
        .iter()
        .filter(|v| {
            !variables.contains_key(v.name.as_str())
                && !item_content.contains(&format!("{{{}}}", v.name))
        })
        .map(|v| v.name.clone())
        .collect()
}

fn eval_conditional(condition: &str, variables: &HashMap<&str, String>) -> bool {
    crate::prompts::template_parsing::eval_conditional_impl(condition, variables)
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
            &result_after_sub,
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

        let partial_names: Vec<String> = extract_partials(&result);

        for partial_name in partial_names.into_iter().rev() {
            let full_match = format!("{{{{> {}}}}}", partial_name);

            if visited.contains(&partial_name) {
                let chain = visited
                    .iter()
                    .rev()
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(" -> ");
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
            &result_after_sub,
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
        let partial_names: Vec<String> = extract_partials(&result);

        for partial_name in partial_names.into_iter().rev() {
            let full_match = format!("{{{{> {}}}}}", partial_name);

            if visited.contains(&partial_name) {
                let chain = visited
                    .iter()
                    .rev()
                    .cloned()
                    .collect::<Vec<_>>()
                    .join(" -> ");
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
            content: Self::restore_literal_segments(&result_after_sub, &literal_segments),
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

                if let Some((header, body)) = parse_loop_header(full_match) {
                    if let Some(var_name) = header.split_whitespace().next() {
                        if let Some(values) = variables.get(var_name) {
                            let items = split_loop_items(values);
                            let (rendered_items, unsubstituted_blocks): (
                                Vec<String>,
                                Vec<Vec<String>>,
                            ) = items
                                .iter()
                                .map(|item| {
                                    let item_content =
                                        render_loop_item(body, item, var_name, variables);
                                    let unsubstituted =
                                        find_unsubstituted_vars(&item_content, variables);
                                    (item_content, unsubstituted)
                                })
                                .unzip();
                            let unsubstituted = unsubstituted_blocks
                                .into_iter()
                                .flatten()
                                .collect::<Vec<_>>();

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

        loop {
            let Some(if_start) = result.find("{% if ") else {
                break;
            };

            // Find the end of the {% if CONDITION %} tag header (first %} after {% if)
            let Some(tag_end_offset) = result[if_start..].find("%}") else {
                break;
            };
            let tag_close = if_start + tag_end_offset + 2; // byte pos right after the closing %}

            // Extract condition string from the tag header
            let tag = &result[if_start..tag_close];
            let Some(cond_end) = tag.find("%}") else {
                break;
            };
            let condition = tag[6..cond_end].trim(); // skip leading "{% if "

            // Find the matching {% endif %} searching from the start of this if block.
            // Simple (non-nested) implementation: first {% endif %} wins.
            let rest_from_if = &result[if_start..];
            let Some(endif_offset) = rest_from_if.find("{% endif %}") else {
                break;
            };
            let endif_abs = if_start + endif_offset;
            let full_end = endif_abs + 11; // "{% endif %}" is 11 chars

            // Clone full_match to avoid borrow-after-mutate
            let full_match = result[if_start..full_end].to_string();

            // Body is between end of tag header and start of {% endif %}
            let body_and_maybe_else = &result[tag_close..endif_abs];

            // Check for {% else %} within the body to split then/else branches
            let replacement = if let Some(else_offset) = body_and_maybe_else.find("{% else %}") {
                let then_body = &body_and_maybe_else[..else_offset];
                let else_body = &body_and_maybe_else[else_offset + 10..]; // 10 = "{% else %}"
                if eval_conditional(condition, variables) {
                    then_body.trim().to_string()
                } else {
                    else_body.trim().to_string()
                }
            } else if eval_conditional(condition, variables) {
                body_and_maybe_else.trim().to_string()
            } else {
                String::new()
            };

            result = result.replacen(&full_match, &replacement, 1);
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
            let raw_token = format!("{{{{{}}}}}", var.placeholder);
            let clean_token = format!("{{{{{}}}}}", var.name);

            let token_to_replace = if result.contains(&raw_token) {
                raw_token.clone()
            } else if result.contains(&clean_token) {
                clean_token.clone()
            } else {
                continue;
            };

            if let Some(value) = variables.get(var.name.as_str()) {
                result = result.replace(&token_to_replace, value);
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
                let default_val = var.default_value.clone().unwrap_or_default();
                result = result.replace(&token_to_replace, &default_val);
                substituted.push(SubstitutionEntry {
                    name: var.name.clone(),
                    source: crate::prompts::template_validator::SubstitutionSource::Default,
                });
            } else {
                unsubstituted.push(var.name.clone());
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

// =========================================================================
// Resume context note generation - boundary module.
// =========================================================================

use crate::checkpoint::execution_history::StepOutcome;
use crate::checkpoint::restore::ResumeContext;
use crate::checkpoint::state::PipelinePhase;
use std::fmt::Write as FmtWrite;

fn format_resume_state(resume_count: u32, rebase_state: &str) -> String {
    crate::prompts::template_parsing::format_resume_state_impl(resume_count, rebase_state)
}

fn format_modified_files_summary(
    detail: &crate::checkpoint::execution_history::ModifiedFilesDetail,
) -> String {
    crate::prompts::template_parsing::format_files_summary_impl(detail).unwrap_or_default()
}

fn format_issues_summary(issues: &crate::checkpoint::execution_history::IssuesSummary) -> String {
    crate::prompts::template_parsing::format_issues_summary_impl(issues).unwrap_or_default()
}

fn format_recent_step(step: &crate::checkpoint::execution_history::ExecutionStep) -> String {
    let mut s = format!(
        "- [{}] {} (iteration {}): {}\n",
        step.step_type,
        step.phase,
        step.iteration,
        step.outcome.brief_description()
    );

    if let Some(ref detail) = step.modified_files_detail {
        s.push_str(&format_modified_files_summary(detail));
    }

    if let Some(ref issues) = step.issues_summary {
        s.push_str(&format_issues_summary(issues));
    }

    if let Some(ref oid) = step.git_commit_oid {
        s.push_str(&format!("  Commit: {oid}\n"));
    }

    s
}

fn format_recent_activity(
    history: &crate::checkpoint::execution_history::ExecutionHistory,
) -> String {
    let recent_steps: Vec<_> = history
        .steps
        .iter()
        .rev()
        .take(5)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect();

    let steps_str: String = recent_steps.iter().map(|s| format_recent_step(s)).collect();
    format!("RECENT ACTIVITY:\n----------------\n{steps_str}\n")
}

fn append_resume_and_rebase_state(note: &mut String, context: &ResumeContext) {
    let rebase_str = format!("{:?}", context.rebase_state);
    let formatted = format_resume_state(context.resume_count, &rebase_str);
    note.push_str(&formatted);
    note.push('\n');
}

fn append_recent_activity(note: &mut String, context: &ResumeContext) {
    let Some(ref history) = context.execution_history else {
        return;
    };
    if history.steps.is_empty() {
        return;
    }
    note.push_str(&format_recent_activity(history));
    note.push('\n');
}

fn append_guidance(note: &mut String, phase: PipelinePhase) {
    note.push_str("\nGUIDANCE:\n");
    note.push_str("--------\n");
    match phase {
        PipelinePhase::Development => {
            note.push_str("Continue working on the implementation tasks from your plan.\n");
        }
        PipelinePhase::Review => {
            note.push_str("Review the code changes and provide feedback.\n");
        }
        _ => {}
    }
    note.push('\n');
}

/// Generate a rich resume note from resume context.
///
/// Creates a detailed, context-aware note that helps agents understand
/// where they are in the pipeline when resuming from a checkpoint.
///
/// The note includes:
/// - Phase and iteration information
/// - Recent execution history (files modified, issues found/fixed)
/// - Git commits made during the session
/// - Guidance on what to focus on
#[must_use]
pub fn generate_resume_note(context: &ResumeContext) -> String {
    let mut note = String::from("SESSION RESUME CONTEXT\n");
    note.push_str("====================\n\n");

    match context.phase {
        PipelinePhase::Development => {
            let _ = writeln!(
                note,
                "Resuming DEVELOPMENT phase (iteration {} of {})",
                context.iteration + 1,
                context.total_iterations
            );
        }
        PipelinePhase::Review => {
            let _ = writeln!(
                note,
                "Resuming REVIEW phase (pass {} of {})",
                context.reviewer_pass + 1,
                context.total_reviewer_passes
            );
        }
        _ => {
            let _ = writeln!(note, "Resuming from phase: {}", context.phase_name());
        }
    }

    append_resume_and_rebase_state(&mut note, context);
    append_recent_activity(&mut note, context);

    note.push_str("Previous progress is preserved in git history.\n");
    append_guidance(&mut note, context.phase);
    note
}

/// Helper trait for brief outcome descriptions.
pub trait BriefDescription {
    fn brief_description(&self) -> String;
}

impl BriefDescription for StepOutcome {
    fn brief_description(&self) -> String {
        use crate::prompts::template_parsing::OutcomeDescription;
        match self {
            Self::Success {
                files_modified,
                output,
                ..
            } => {
                let files: Option<Vec<String>> = files_modified.as_ref().map(|b| b.to_vec());
                let output_str: Option<String> = output.as_ref().map(|b| b.to_string());
                OutcomeDescription::from_outcome(
                    &files,
                    &output_str,
                    &None,
                    &None,
                    &None,
                    &None,
                    &None,
                )
                .as_string()
            }
            Self::Failure {
                error, recoverable, ..
            } => {
                let error_str: Option<String> = Some((*error).to_string());
                let recoverable_val = Some(*recoverable);
                let recoverable_ref = &recoverable_val;
                OutcomeDescription::from_outcome(
                    &None,
                    &None,
                    &error_str,
                    recoverable_ref,
                    &None,
                    &None,
                    &None,
                )
                .failure_recoverable
                .or_else(|| {
                    OutcomeDescription::from_outcome(
                        &None,
                        &None,
                        &error_str,
                        recoverable_ref,
                        &None,
                        &None,
                        &None,
                    )
                    .failure_fatal
                })
                .unwrap_or_default()
            }
            Self::Partial {
                completed,
                remaining,
                ..
            } => {
                let completed_str: Option<String> = Some((*completed).to_string());
                let remaining_str: Option<String> = Some((*remaining).to_string());
                OutcomeDescription::from_outcome(
                    &None,
                    &None,
                    &None,
                    &None,
                    &completed_str,
                    &remaining_str,
                    &None,
                )
                .partial
                .unwrap_or_default()
            }
            Self::Skipped { reason } => {
                let reason_str: Option<String> = Some((*reason).to_string());
                OutcomeDescription::from_outcome(
                    &None,
                    &None,
                    &None,
                    &None,
                    &None,
                    &None,
                    &reason_str,
                )
                .skipped
                .unwrap_or_default()
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_render_loop_item_substitutes_variables_and_item() {
        let body = "Name: {item}, Age: {{age}}, City: {{city}}";
        let item = "Alice";
        let var_name = "item";
        let mut variables = HashMap::new();
        variables.insert("age", "30".to_string());
        variables.insert("city", "NYC".to_string());

        let result = render_loop_item(body, item, var_name, &variables);

        assert_eq!(result, "Name: Alice, Age: 30, City: NYC");
    }

    #[test]
    fn test_render_loop_item_no_variables() {
        let body = "Item: {item}";
        let item = "value";
        let var_name = "item";
        let variables = HashMap::new();

        let result = render_loop_item(body, item, var_name, &variables);

        assert_eq!(result, "Item: value");
    }

    #[test]
    fn test_render_loop_item_no_item_placeholder() {
        let body = "Age: {{age}}";
        let item = "unused";
        let var_name = "item";
        let mut variables = HashMap::new();
        variables.insert("age", "25".to_string());

        let result = render_loop_item(body, item, var_name, &variables);

        assert_eq!(result, "Age: 25");
    }
}
