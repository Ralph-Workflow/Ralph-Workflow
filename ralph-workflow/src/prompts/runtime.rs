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
    let mut item_content = body.to_string();
    for (key, val) in variables {
        item_content = item_content.replace(&format!("{{{{{}}}}}", key), val);
    }
    item_content.replace(&format!("{{{}}}", var_name), item)
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
                    if let Some(var_name) = header.trim().split_whitespace().next() {
                        if let Some(values) = variables.get(var_name) {
                            let items = split_loop_items(values);
                            let mut rendered_items = Vec::new();
                            let mut unsubstituted = Vec::new();

                            for item in items {
                                let item_content =
                                    render_loop_item(body, item, var_name, variables);
                                unsubstituted
                                    .extend(find_unsubstituted_vars(&item_content, variables));
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
                        (
                            eval_conditional(cond, variables),
                            &full_match[body_start..else_pos],
                        )
                    } else if let Some(endif_pos) = endif_block {
                        let cond = condition.trim();
                        (
                            eval_conditional(cond, variables),
                            &full_match[body_start..endif_pos],
                        )
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

        while result.contains("{% endif %}") {
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
                let files: Option<Vec<String>> =
                    files_modified.as_ref().map(|b| b.iter().cloned().collect());
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
