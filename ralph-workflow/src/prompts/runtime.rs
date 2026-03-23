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

struct PartialExpandState<'a> {
    result: &'a mut String,
    literal_segments: &'a mut Vec<LiteralSegment>,
    log: &'a mut SubstitutionLog,
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

struct LoopMatchResult {
    full_match: String,
    var_name: String,
    body: String,
}

fn try_parse_loop_for_tag(result: &str, for_start: usize) -> Option<LoopMatchResult> {
    let for_end = result[for_start..].find("%}")?;
    let for_end = for_start + for_end;
    let full_match = result[for_start..for_end + 2].to_string();
    let full_match_clone = full_match.clone();
    let (header, body) = parse_loop_header(&full_match_clone)?;
    let var_name = header.split_whitespace().next()?.to_string();
    Some(LoopMatchResult {
        full_match,
        var_name,
        body: body.to_string(),
    })
}

fn render_loop_items(
    body: &str,
    var_name: &str,
    values: &str,
    variables: &HashMap<&str, String>,
) -> (Vec<String>, Vec<String>) {
    let items = split_loop_items(values);
    let (rendered_items, unsubstituted_blocks): (Vec<String>, Vec<Vec<String>>) = items
        .iter()
        .map(|item| {
            let item_content = render_loop_item(body, item, var_name, variables);
            let unsubstituted = find_unsubstituted_vars(&item_content, variables);
            (item_content, unsubstituted)
        })
        .unzip();
    let unsubstituted = unsubstituted_blocks.into_iter().flatten().collect();
    (rendered_items, unsubstituted)
}

fn find_conditional_block(result: &str, if_start: usize) -> Option<(usize, usize, String)> {
    let tag_end_offset = result[if_start..].find("%}")?;
    let tag_close = if_start + tag_end_offset + 2;
    let rest_from_if = &result[if_start..];
    let endif_offset = rest_from_if.find("{% endif %}")?;
    let endif_abs = if_start + endif_offset;
    let full_end = endif_abs + 11;
    let full_match = result[if_start..full_end].to_string();
    Some((tag_close, endif_abs, full_match))
}

fn eval_conditional_body(
    condition: &str,
    body: &str,
    else_body: Option<&str>,
    variables: &HashMap<&str, String>,
) -> String {
    if eval_conditional(condition, variables) {
        body.trim().to_string()
    } else {
        else_body.map(str::trim).unwrap_or("").to_string()
    }
}

fn eval_conditional_replacement(
    condition: &str,
    body_and_maybe_else: &str,
    variables: &HashMap<&str, String>,
) -> String {
    if let Some(else_offset) = body_and_maybe_else.find("{% else %}") {
        eval_conditional_body(
            condition,
            &body_and_maybe_else[..else_offset],
            Some(&body_and_maybe_else[else_offset + 10..]),
            variables,
        )
    } else {
        eval_conditional_body(condition, body_and_maybe_else, None, variables)
    }
}

fn process_one_conditional(result: &str, variables: &HashMap<&str, String>) -> Option<String> {
    let if_start = result.find("{% if ")?;
    let tag = {
        let tag_end_offset = result[if_start..].find("%}")?;
        let tag_close = if_start + tag_end_offset + 2;
        result[if_start..tag_close].to_string()
    };
    let cond_end = tag.find("%}")?;
    let condition = tag[6..cond_end].trim().to_string();
    let (tag_close, endif_abs, full_match) = find_conditional_block(result, if_start)?;
    let body_and_maybe_else = result[tag_close..endif_abs].to_string();
    let replacement = eval_conditional_replacement(&condition, &body_and_maybe_else, variables);
    Some(result.replacen(&full_match, &replacement, 1))
}

struct VarSubResult {
    new_result: String,
    entry: Option<SubstitutionEntry>,
    unsubstituted: Option<String>,
}

fn determine_substitution_source(value: &str, has_default: bool) -> SubstitutionSource {
    if value.is_empty() {
        SubstitutionSource::EmptyWithDefault
    } else if has_default {
        SubstitutionSource::Default
    } else {
        SubstitutionSource::Value
    }
}

fn sub_result_with_value(
    result: &str,
    token: &str,
    name: &str,
    value: &str,
    source: SubstitutionSource,
) -> VarSubResult {
    VarSubResult {
        new_result: result.replace(token, value),
        entry: Some(SubstitutionEntry {
            name: name.to_string(),
            source,
        }),
        unsubstituted: None,
    }
}

fn sub_result_with_default(
    result: &str,
    token: &str,
    name: &str,
    default_val: String,
) -> VarSubResult {
    VarSubResult {
        new_result: result.replace(token, &default_val),
        entry: Some(SubstitutionEntry {
            name: name.to_string(),
            source: SubstitutionSource::Default,
        }),
        unsubstituted: None,
    }
}

fn sub_result_unresolved(result: &str, name: &str) -> VarSubResult {
    VarSubResult {
        new_result: result.to_string(),
        entry: None,
        unsubstituted: Some(name.to_string()),
    }
}

fn sub_result_unresolved_none(result: &str) -> VarSubResult {
    VarSubResult {
        new_result: result.to_string(),
        entry: None,
        unsubstituted: None,
    }
}

fn resolve_variable_substitution(
    result: &str,
    var: &crate::prompts::template_validator::VariableInfo,
    variables: &HashMap<&str, String>,
    token: &str,
) -> VarSubResult {
    if let Some(value) = variables.get(var.name.as_str()) {
        let source = determine_substitution_source(value, var.has_default);
        sub_result_with_value(result, token, &var.name, value, source)
    } else if var.has_default {
        let default_val = var.default_value.clone().unwrap_or_default();
        sub_result_with_default(result, token, &var.name, default_val)
    } else {
        sub_result_unresolved(result, &var.name)
    }
}

fn find_variable_token(result: &str, placeholder: &str, name: &str) -> Option<String> {
    let raw_token = format!("{{{{{}}}}}", placeholder);
    let clean_token = format!("{{{{{}}}}}", name);
    if result.contains(&raw_token) {
        Some(raw_token)
    } else if result.contains(&clean_token) {
        Some(clean_token)
    } else {
        None
    }
}

fn substitute_one_variable(
    result: &str,
    var: &crate::prompts::template_validator::VariableInfo,
    variables: &HashMap<&str, String>,
) -> VarSubResult {
    find_variable_token(result, &var.placeholder, &var.name).map_or_else(
        || sub_result_unresolved_none(result),
        |token| resolve_variable_substitution(result, var, variables, &token),
    )
}

fn build_circular_reference_chain(visited: &[String]) -> String {
    visited
        .iter()
        .rev()
        .cloned()
        .collect::<Vec<_>>()
        .join(" -> ")
}

fn collect_missing_from_loop_logs(
    loop_logs: &[LoopRenderLog],
    result: &str,
    unsubstituted: Vec<String>,
) -> Vec<String> {
    loop_logs
        .iter()
        .filter(|ll| result.contains(&ll.token))
        .flat_map(|ll| ll.unsubstituted.clone())
        .chain(unsubstituted)
        .collect::<std::collections::HashSet<_>>()
        .into_iter()
        .collect()
}

fn extend_log_with_loop_logs(
    log: &mut SubstitutionLog,
    loop_logs: Vec<LoopRenderLog>,
    result: &str,
) {
    for loop_log in loop_logs {
        if !result.contains(&loop_log.token) {
            continue;
        }
        log.substituted.extend(loop_log.substituted);
        let new_unsub: Vec<String> = loop_log
            .unsubstituted
            .into_iter()
            .filter(|name| !log.unsubstituted.contains(name))
            .collect();
        log.unsubstituted.extend(new_unsub);
    }
}

fn extend_log_dedup(
    log: &mut SubstitutionLog,
    substituted: Vec<SubstitutionEntry>,
    unsubstituted: Vec<String>,
) {
    log.substituted.extend(substituted);
    let new_unsub: Vec<String> = unsubstituted
        .into_iter()
        .filter(|name| !log.unsubstituted.contains(name))
        .collect();
    log.unsubstituted.extend(new_unsub);
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

    fn expand_one_partial(
        &self,
        partial_name: &str,
        result: &mut String,
        literal_segments: &mut Vec<LiteralSegment>,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
        visited: &mut Vec<String>,
    ) -> Result<(), TemplateError> {
        if visited.contains(&partial_name.to_string()) {
            return Err(TemplateError::CircularReference(
                build_circular_reference_chain(visited),
            ));
        }
        let partial_content = partials
            .get(partial_name)
            .ok_or_else(|| TemplateError::PartialNotFound(partial_name.to_string()))?;
        let partial_template = Self::new(partial_content);
        visited.push(partial_name.to_string());
        let rendered =
            partial_template.render_with_partials_recursive(variables, partials, visited)?;
        visited.pop();
        let full_match = format!("{{{{> {}}}}}", partial_name);
        let token = Self::next_literal_token(result, &rendered, literal_segments);
        literal_segments.push(LiteralSegment {
            token: token.clone(),
            content: rendered,
        });
        *result = result.replace(&full_match, &token);
        Ok(())
    }

    fn process_rendered_content(
        result: &str,
        variables: &HashMap<&str, String>,
        literal_segments: &mut Vec<LiteralSegment>,
    ) -> Result<String, TemplateError> {
        let (loop_result, loop_logs) =
            Self::process_loops_with_log(result, variables, literal_segments);
        let after_cond = Self::process_conditionals(&loop_result, variables);
        let (result_after_sub, _substituted, unsubstituted) =
            Self::substitute_variables_allow_empty(&after_cond, variables);
        let missing = collect_missing_from_loop_logs(&loop_logs, &after_cond, unsubstituted);
        if let Some(first_missing) = missing.first() {
            return Err(TemplateError::MissingVariable(first_missing.clone()));
        }
        Ok(Self::restore_literal_segments(
            &result_after_sub,
            literal_segments,
        ))
    }

    fn render_with_partials_recursive(
        &self,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
        visited: &mut Vec<String>,
    ) -> Result<String, TemplateError> {
        let mut literal_segments = Vec::new();
        let mut result = self.content.clone();
        for partial_name in extract_partials(&result).into_iter().rev() {
            self.expand_one_partial(
                &partial_name,
                &mut result,
                &mut literal_segments,
                variables,
                partials,
                visited,
            )?;
        }
        Self::process_rendered_content(&result, variables, &mut literal_segments)
    }

    fn expand_one_partial_with_log(
        &self,
        partial_name: &str,
        template_name: &str,
        state: &mut PartialExpandState<'_>,
        variables: &HashMap<&str, String>,
        partials: &HashMap<String, String>,
        visited: &mut Vec<String>,
    ) -> Result<(), TemplateError> {
        if visited.contains(&partial_name.to_string()) {
            return Err(TemplateError::CircularReference(
                build_circular_reference_chain(visited),
            ));
        }
        let partial_content = partials
            .get(partial_name)
            .ok_or_else(|| TemplateError::PartialNotFound(partial_name.to_string()))?;
        let partial_template = Self::new(partial_content);
        visited.push(partial_name.to_string());
        let rendered = partial_template.render_with_log_recursive(
            template_name,
            variables,
            partials,
            visited,
        )?;
        visited.pop();
        let full_match = format!("{{{{> {}}}}}", partial_name);
        let token =
            Self::next_literal_token(state.result, &rendered.content, state.literal_segments);
        state.literal_segments.push(LiteralSegment {
            token: token.clone(),
            content: rendered.content,
        });
        *state.result = state.result.replace(&full_match, &token);
        extend_log_dedup(
            state.log,
            rendered.log.substituted,
            rendered.log.unsubstituted,
        );
        Ok(())
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
        for partial_name in extract_partials(&result).into_iter().rev() {
            let mut state = PartialExpandState {
                result: &mut result,
                literal_segments: &mut literal_segments,
                log: &mut log,
            };
            self.expand_one_partial_with_log(
                &partial_name,
                template_name,
                &mut state,
                variables,
                partials,
                visited,
            )?;
        }
        let (loop_result, loop_logs) =
            Self::process_loops_with_log(&result, variables, &mut literal_segments);
        let result = Self::process_conditionals(&loop_result, variables);
        extend_log_with_loop_logs(&mut log, loop_logs, &result);
        let (result_after_sub, substituted, unsubstituted) =
            Self::substitute_variables(&result, variables);
        extend_log_dedup(&mut log, substituted, unsubstituted);
        Ok(RenderedTemplate {
            content: Self::restore_literal_segments(&result_after_sub, &literal_segments),
            log,
        })
    }

    fn process_one_loop(
        result: &str,
        variables: &HashMap<&str, String>,
        literal_segments: &mut Vec<LiteralSegment>,
        token_counter: &mut usize,
    ) -> Option<(String, LoopRenderLog)> {
        let for_start = result.find("{% for ")?;
        let parsed = try_parse_loop_for_tag(result, for_start)?;
        let values = variables.get(parsed.var_name.as_str())?;
        let (rendered_items, unsubstituted) =
            render_loop_items(&parsed.body, &parsed.var_name, values, variables);
        let loop_token = format!("__LOOP_TOKEN_{}__", token_counter);
        *token_counter += 1;
        literal_segments.push(LiteralSegment {
            token: loop_token.clone(),
            content: rendered_items.join("\n"),
        });
        let log = LoopRenderLog {
            token: loop_token.clone(),
            substituted: vec![SubstitutionEntry {
                name: parsed.var_name.clone(),
                source: crate::prompts::template_validator::SubstitutionSource::Value,
            }],
            unsubstituted,
        };
        Some((result.replace(&parsed.full_match, &loop_token), log))
    }

    fn process_loops_with_log(
        content: &str,
        variables: &HashMap<&str, String>,
        literal_segments: &mut Vec<LiteralSegment>,
    ) -> (String, Vec<LoopRenderLog>) {
        let mut result = content.to_string();
        let mut loop_logs = Vec::new();
        let mut token_counter = 0;
        while let Some((new_result, log)) =
            Self::process_one_loop(&result, variables, literal_segments, &mut token_counter)
        {
            result = new_result;
            loop_logs.push(log);
        }
        (result, loop_logs)
    }

    fn process_conditionals(content: &str, variables: &HashMap<&str, String>) -> String {
        let mut result = content.to_string();
        while let Some(new_result) = process_one_conditional(&result, variables) {
            result = new_result;
        }
        result
    }

    fn substitute_variables_allow_empty(
        content: &str,
        variables: &HashMap<&str, String>,
    ) -> (String, Vec<SubstitutionEntry>, Vec<String>) {
        let vars = extract_variables(content);
        vars.iter().fold(
            (content.to_string(), Vec::new(), Vec::new()),
            |(result, mut substituted, mut unsubstituted), var| {
                let sub = substitute_one_variable(&result, var, variables);
                if let Some(entry) = sub.entry {
                    substituted.push(entry);
                }
                if let Some(name) = sub.unsubstituted {
                    unsubstituted.push(name);
                }
                (sub.new_result, substituted, unsubstituted)
            },
        )
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

fn optional_files_summary(
    detail: &Option<crate::checkpoint::execution_history::ModifiedFilesDetail>,
) -> String {
    detail
        .as_ref()
        .map_or(String::new(), format_modified_files_summary)
}

fn optional_issues_summary(
    issues: &Option<crate::checkpoint::execution_history::IssuesSummary>,
) -> String {
    issues.as_ref().map_or(String::new(), format_issues_summary)
}

fn optional_commit_line(oid: &Option<String>) -> String {
    oid.as_deref()
        .map_or(String::new(), |o| format!("  Commit: {o}\n"))
}

fn format_recent_step(step: &crate::checkpoint::execution_history::ExecutionStep) -> String {
    format!(
        "- [{}] {} (iteration {}): {}\n{}{}{}",
        step.step_type,
        step.phase,
        step.iteration,
        step.outcome.brief_description(),
        optional_files_summary(&step.modified_files_detail),
        optional_issues_summary(&step.issues_summary),
        optional_commit_line(&step.git_commit_oid),
    )
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

fn append_phase_header(note: &mut String, context: &ResumeContext) {
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
    append_phase_header(&mut note, context);
    append_resume_and_rebase_state(&mut note, context);
    append_recent_activity(&mut note, context);
    note.push_str("Previous progress is preserved in git history.\n");
    append_guidance(&mut note, context.phase);
    note
}

fn brief_description_success(
    files_modified: &Option<Box<[String]>>,
    output: &Option<Box<str>>,
) -> String {
    use crate::prompts::template_parsing::OutcomeDescription;
    let files: Option<Vec<String>> = files_modified.as_ref().map(|b| b.to_vec());
    let output_str: Option<String> = output.as_ref().map(|b| b.to_string());
    OutcomeDescription::from_outcome(&files, &output_str, &None, &None, &None, &None, &None)
        .as_string()
}

fn brief_description_failure(error: &str, recoverable: bool) -> String {
    use crate::prompts::template_parsing::OutcomeDescription;
    let error_str: Option<String> = Some(error.to_string());
    let recoverable_val = Some(recoverable);
    let desc = OutcomeDescription::from_outcome(
        &None,
        &None,
        &error_str,
        &recoverable_val,
        &None,
        &None,
        &None,
    );
    desc.failure_recoverable
        .or(desc.failure_fatal)
        .unwrap_or_default()
}

fn brief_description_partial(completed: &str, remaining: &str) -> String {
    use crate::prompts::template_parsing::OutcomeDescription;
    let completed_str: Option<String> = Some(completed.to_string());
    let remaining_str: Option<String> = Some(remaining.to_string());
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

fn brief_description_skipped(reason: &str) -> String {
    use crate::prompts::template_parsing::OutcomeDescription;
    let reason_str: Option<String> = Some(reason.to_string());
    OutcomeDescription::from_outcome(&None, &None, &None, &None, &None, &None, &reason_str)
        .skipped
        .unwrap_or_default()
}

/// Helper trait for brief outcome descriptions.
pub trait BriefDescription {
    fn brief_description(&self) -> String;
}

impl BriefDescription for StepOutcome {
    fn brief_description(&self) -> String {
        match self {
            Self::Success {
                files_modified,
                output,
                ..
            } => brief_description_success(files_modified, output),
            Self::Failure {
                error, recoverable, ..
            } => brief_description_failure(error, *recoverable),
            Self::Partial {
                completed,
                remaining,
                ..
            } => brief_description_partial(completed, remaining),
            Self::Skipped { reason } => brief_description_skipped(reason),
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
