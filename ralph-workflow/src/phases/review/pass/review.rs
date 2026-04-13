// Legacy phase-based code - deprecated in favor of reducer/handler architecture
use super::super::types::{ParseResult, ReviewPassResult};
use super::super::xml_processing::extract_and_validate_review_output_xml;
use super::helpers::{handle_postflight_validation, stderr_contains_auth_error};

use crate::agents::session::{CapabilitySet, PolicyFlagSet, SessionDrain};
use crate::checkpoint::execution_history::{ExecutionStep, StepOutcome};
use crate::files::delete_issues_file_for_isolation_with_workspace;
use crate::files::{artifact_paths, has_valid_artifact_output};
use crate::phases::context::PhaseContext;
use crate::phases::timing::{capture_time, elapsed_seconds};
use crate::pipeline::{run_with_prompt, PipelineRuntime, PromptCommand};
use crate::prompts::{
    prompt_review_xml_with_references_and_log, PromptContentBuilder, SessionCapabilities,
};
use anyhow::Context as _;

use std::path::Path;

/// Run the review pass for a single cycle.
///
/// This function orchestrates a single review pass that validates code changes
/// and extracts issues to ISSUES.md. It handles:
///
/// - Prompt generation with checkpoint replay support
/// - Agent invocation with proper logging and configuration
/// - XML output extraction and validation
/// - Execution history tracking
///
/// # Arguments
///
/// * `ctx` - Phase context with workspace, logger, and configuration
/// * `j` - The cycle number (used for logging and prompt keys)
/// * `review_label` - Human-readable label for this review pass
/// * `_review_prompt` - Unused (kept for API compatibility)
/// * `agent` - Optional agent override (defaults to `ctx.reviewer_agent`)
///
/// # Returns
///
/// A `ReviewPassResult` indicating whether issues were found and if the pass succeeded.
///
/// # Errors
///
/// Returns an error if:
/// - Agent configuration is missing
/// - Prompt template contains unresolved placeholders
/// - Log file cannot be written
///
/// # Panics
///
/// Panics if invariants are violated.
pub fn run_review_pass(
    ctx: &mut PhaseContext<'_>,
    j: u32,
    review_label: &str,
    _review_prompt: &str, // Unused - we build XML prompt internally
    agent: Option<&str>,
) -> anyhow::Result<ReviewPassResult> {
    let active_agent = agent.unwrap_or(ctx.reviewer_agent);
    let issues_path = Path::new(".agent/ISSUES.md");

    let plan_content = ctx
        .workspace
        .read(Path::new(".agent/PLAN.md"))
        .unwrap_or_default();

    let (changes_content, baseline_oid_for_prompts) =
        match crate::git_helpers::get_git_diff_for_review_with_workspace(ctx.workspace) {
            Ok((diff, baseline_oid)) => (diff, baseline_oid),
            Err(e) => {
                ctx.logger
                    .warn(&format!("Failed to get baseline diff for review: {e}"));
                (String::new(), String::new())
            }
        };

    let refs = PromptContentBuilder::new(ctx.workspace)
        .with_plan(plan_content)
        .with_diff(changes_content, &baseline_oid_for_prompts)
        .build();
    let capabilities = CapabilitySet::defaults_for_drain(SessionDrain::Review);
    let policy_flags = PolicyFlagSet::defaults_for_drain(SessionDrain::Review);
    let rendered = prompt_review_xml_with_references_and_log(
        ctx.template_context,
        &refs,
        ctx.workspace,
        "review_xml",
        SessionCapabilities::new(&capabilities, &policy_flags),
    );
    let review_prompt_xml = rendered.content;

    // Legacy phase-based code
    // Validate freshly rendered prompts using substitution logs (no regex scanning).
    if !rendered.log.is_complete() {
        return Err(anyhow::anyhow!(
            "Review prompt has unresolved placeholders: {:?}",
            rendered.log.unsubstituted
        ));
    }

    if ctx.config.verbosity.is_debug() {
        ctx.logger.info(&format!(
            "Review prompt length: {} characters",
            review_prompt_xml.len()
        ));
    }

    // Use per-run log directory with simplified naming
    let base_log_path = ctx.run_log_context.agent_log("reviewer", j, None);
    let attempt = crate::pipeline::logfile::next_simplified_logfile_attempt_index(
        &base_log_path,
        ctx.workspace,
    );
    let logfile = if attempt == 0 {
        base_log_path
            .to_str()
            .expect("Path contains invalid UTF-8 - all paths in this codebase should be UTF-8")
            .to_string()
    } else {
        ctx.run_log_context
            .agent_log("reviewer", j, Some(attempt))
            .to_str()
            .expect("Path contains invalid UTF-8 - all paths in this codebase should be UTF-8")
            .to_string()
    };

    // Write log file header with agent metadata
    // Use append_bytes to avoid overwriting if file exists (defense-in-depth)
    let log_header = format!(
        "# Ralph Agent Invocation Log\n\
         # Role: Reviewer\n\
         # Agent: {}\n\
         # Model Index: 0\n\
         # Attempt: {}\n\
         # Phase: Review\n\
         # Timestamp: {}\n\n",
        active_agent,
        attempt,
        chrono::Utc::now().to_rfc3339()
    );
    ctx.workspace
        .append_bytes(std::path::Path::new(&logfile), log_header.as_bytes())
        .context("Failed to write agent log header - log would be incomplete without metadata")?;

    let log_prefix = format!("reviewer_{j}"); // For attribution only
    let model_index = 0usize; // Default model index for attribution

    let agent_config = ctx
        .registry
        .resolve_config(active_agent)
        .ok_or_else(|| anyhow::anyhow!("Agent not found: {active_agent}"))?;
    let cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    let prompt_cmd = PromptCommand {
        label: review_label,
        display_name: active_agent,
        cmd_str: &cmd_str,
        prompt: &review_prompt_xml,
        log_prefix: &log_prefix,
        model_index: Some(model_index),
        attempt: Some(attempt),
        logfile: &logfile,
        parser_type: agent_config.json_parser,
        env_vars: &agent_config.env_vars,
        completion_output_path: Some(Path::new(artifact_paths::ISSUES_XML)),
    };

    let attempt_start = capture_time();
    let result = run_with_prompt(
        &prompt_cmd,
        &mut PipelineRuntime {
            timer: ctx.timer,
            logger: ctx.logger,
            colors: ctx.colors,
            config: ctx.config,
            executor: ctx.executor,
            executor_arc: std::sync::Arc::clone(&ctx.executor_arc),
            workspace: ctx.workspace,
            workspace_arc: std::sync::Arc::clone(&ctx.workspace_arc),
        },
    )?;
    if result.exit_code != 0 {
        let auth_failure = stderr_contains_auth_error(&result.stderr);
        // Auth errors always fail regardless of output file state.
        if auth_failure {
            return Ok(ReviewPassResult::agent_failed(true));
        }
        // Non-auth non-zero exit: fail only when no valid result file exists.
        // A valid ISSUES_JSON despite non-zero exit means the agent completed
        // its work (e.g., proprietary exit codes like reason:91 from OpenCode).
        if !has_valid_artifact_output(
            ctx.workspace,
            Path::new(artifact_paths::ISSUES_JSON),
        ) {
            return Ok(ReviewPassResult::agent_failed(false));
        }
    }

    let parse_result = extract_and_validate_review_output_xml(ctx, &log_prefix, issues_path)?;

    match parse_result {
        ParseResult::IssuesFound {
            issues,
            xml_content,
        } => {
            handle_postflight_validation(ctx, j);

            ctx.logger
                .success(&format!("Issues extracted: {} total", issues.len()));

            let step = ExecutionStep::new(
                "Review",
                j,
                "review",
                StepOutcome::success(
                    Some(format!("{} issues found", issues.len())),
                    vec![".agent/ISSUES.md".to_string()],
                ),
            )
            .with_agent(active_agent)
            .with_duration(elapsed_seconds(attempt_start));
            let _ = ctx
                .execution_history
                .add_step_bounded(step, ctx.config.execution_history_limit);

            Ok(ReviewPassResult::issues_found(xml_content))
        }
        ParseResult::NoIssuesExplicit { xml_content } => {
            ctx.logger
                .success(&format!("No issues found after cycle {j} - stopping early"));

            if ctx.config.isolation_mode {
                delete_issues_file_for_isolation_with_workspace(ctx.workspace, ctx.logger)?;
            }

            let step = ExecutionStep::new(
                "Review",
                j,
                "review",
                StepOutcome::success(Some("No issues found".to_string()), vec![]),
            )
            .with_agent(active_agent)
            .with_duration(elapsed_seconds(attempt_start));
            let _ = ctx
                .execution_history
                .add_step_bounded(step, ctx.config.execution_history_limit);

            Ok(ReviewPassResult::no_issues(xml_content))
        }
        ParseResult::ParseFailed(reason) => {
            ctx.logger
                .warn(&format!("Review output validation failed: {reason}"));

            Ok(ReviewPassResult::output_invalid())
        }
    }
}
