// Legacy phase-based code - deprecated in favor of reducer/handler architecture
use crate::phases::commit_logging::CommitAttemptLog;

/// Outcome of commit message generation.
///
/// This is intentionally an enum so callers must handle skip explicitly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CommitMessageOutcome {
    /// A normal commit message ready to be written to `commit-message.txt`.
    Message(String),
    /// The agent determined there are no changes to commit.
    Skipped { reason: String },
}

/// Result of commit message generation.
#[derive(Debug)]
pub struct CommitMessageResult {
    pub outcome: CommitMessageOutcome,
}

/// Outcome from a single commit attempt.
pub struct CommitAttemptResult {
    pub had_error: bool,
    pub output_valid: bool,
    pub message: Option<String>,
    pub skip_reason: Option<String>,
    pub files: Vec<String>,
    pub excluded_files: Vec<crate::reducer::state::pipeline::ExcludedFile>,
    pub validation_detail: String,
    pub auth_failure: bool,
}

/// Run a single commit generation attempt with explicit agent and prompt.
///
/// This does **not** perform in-session XSD retries. If validation fails, the
/// caller should emit a `MessageValidationFailed` event and let the reducer decide
/// retry/fallback behavior.
///
/// **IMPORTANT:** The `model_safe_diff` parameter must be pre-truncated to the
/// effective model budget. Use the reducer's `MaterializeCommitInputs` effect
/// to truncate the diff before calling this function. The reducer writes the
/// model-safe diff to `.agent/tmp/commit_diff.model_safe.txt`.
///
/// # Panics
///
/// Panics if invariants are violated.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn run_commit_attempt(
    ctx: &mut PhaseContext<'_>,
    attempt: u32,
    model_safe_diff: &str,
    commit_agent: &str,
) -> anyhow::Result<CommitAttemptResult> {
    // NOTE: Truncation is now handled by materialize_commit_inputs in the reducer.
    // The diff passed here is already truncated to the effective model budget.
    // See: reducer/handler/commit.rs::materialize_commit_inputs

    let (prompt, substitution_log) =
        build_commit_prompt(ctx.template_context, model_safe_diff, ctx.workspace);

    // Legacy phase-based code
    // Validate freshly rendered prompts using substitution logs (no regex scanning).
    if !substitution_log.is_complete() {
        return Err(anyhow::anyhow!(
            "Commit prompt has unresolved placeholders: {:?}",
            substitution_log.unsubstituted
        ));
    }

    let log_dir = ctx
        .run_log_context
        .run_dir()
        .join("debug")
        .join("commit_generation");
    let (session, attempt_number) =
        crate::phases::commit::runner::io::create_session_and_get_attempt_number(
            &log_dir,
            ctx.workspace,
        );
    let diff_was_truncated =
        model_safe_diff.contains("[Truncated:") || model_safe_diff.contains("[truncated...]");
    let attempt_log = CommitAttemptLog::with_basics(
        attempt_number,
        commit_agent,
        "single",
        prompt.len(),
        model_safe_diff.len(),
        diff_was_truncated,
    );

    let agent_config = ctx
        .registry
        .resolve_config(commit_agent)
        .ok_or_else(|| anyhow::anyhow!("Agent not found: {commit_agent}"))?;
    let cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    // Use per-run log directory with simplified naming
    let base_log_path = ctx.run_log_context.agent_log("commit", attempt, None);
    let log_attempt = crate::pipeline::logfile::next_simplified_logfile_attempt_index(
        &base_log_path,
        ctx.workspace,
    );
    let logfile = if log_attempt == 0 {
        base_log_path
            .to_str()
            .expect("Path contains invalid UTF-8 - all paths in this codebase should be UTF-8")
            .to_string()
    } else {
        ctx.run_log_context
            .agent_log("commit", attempt, Some(log_attempt))
            .to_str()
            .expect("Path contains invalid UTF-8 - all paths in this codebase should be UTF-8")
            .to_string()
    };

    // Write log file header with agent metadata
    // Use append_bytes to avoid overwriting if file exists (defense-in-depth)
    let log_header = format!(
        "# Ralph Agent Invocation Log\n\
         # Role: Commit\n\
         # Agent: {}\n\
         # Model Index: 0\n\
         # Attempt: {}\n\
         # Phase: CommitMessage\n\
         # Timestamp: {}\n\n",
        commit_agent,
        log_attempt,
        chrono::Utc::now().to_rfc3339()
    );
    ctx.workspace
        .append_bytes(std::path::Path::new(&logfile), log_header.as_bytes())
        .context("Failed to write agent log header - log would be incomplete without metadata")?;

    let log_prefix = format!("commit_{attempt}"); // For attribution only
    let model_index = 0usize; // Default model index for attribution
    let prompt_cmd = PromptCommand {
        label: commit_agent,
        display_name: commit_agent,
        cmd_str: &cmd_str,
        prompt: &prompt,
        log_prefix: &log_prefix,
        model_index: Some(model_index),
        attempt: Some(log_attempt),
        logfile: &logfile,
        parser_type: agent_config.json_parser,
        env_vars: &agent_config.env_vars,
    };

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
    let had_error = result.exit_code != 0;
    let auth_failure = had_error && stderr_contains_auth_error(&result.stderr);

    if auth_failure {
        let attempt_log = attempt_log.with_raw_output(&result.stderr);
        if !session.is_noop() {
            let _ = attempt_log.write_to_workspace(session.run_dir(), ctx.workspace);
            let _ = session.write_summary(1, "AUTHENTICATION_FAILURE", ctx.workspace);
        }
        return Ok(CommitAttemptResult {
            had_error,
            output_valid: false,
            message: None,
            skip_reason: None,
            files: vec![],
            excluded_files: vec![],
            validation_detail: "Authentication error detected".to_string(),
            auth_failure: true,
        });
    }

    let attempt_log = attempt_log.with_raw_output(&result.stderr);

    let extraction = extract_commit_message_from_file_with_workspace(ctx.workspace);
    let (outcome, detail, extraction_result, extraction_succeeded, skip_reason, files, excluded) =
        match extraction {
            CommitExtractionOutcome::Valid {
                extracted: result,
                files,
                excluded_files,
            } => (
                AttemptOutcome::Success(result.clone().into_message()),
                "Valid commit message extracted".to_string(),
                Some(result),
                true,
                None,
                files,
                excluded_files,
            ),
            CommitExtractionOutcome::InvalidXml(detail) => (
                AttemptOutcome::XsdValidationFailed(detail.clone()),
                detail,
                None,
                false,
                None,
                vec![],
                vec![],
            ),
            CommitExtractionOutcome::MissingFile(detail) => (
                AttemptOutcome::ExtractionFailed(detail.clone()),
                detail,
                None,
                false,
                None,
                vec![],
                vec![],
            ),
            CommitExtractionOutcome::Skipped(reason) => (
                AttemptOutcome::Success(format!("SKIPPED: {reason}")),
                format!("Commit skipped: {reason}"),
                None,
                true,
                Some(reason),
                vec![],
                vec![],
            ),
        };

    let attempt_log = attempt_log
        .with_extraction_attempt(if extraction_succeeded {
            ExtractionAttempt::success("XML", detail.clone())
        } else {
            ExtractionAttempt::failure("XML", detail.clone())
        })
        .with_outcome(outcome.clone());

    if !session.is_noop() {
        let _ = attempt_log.write_to_workspace(session.run_dir(), ctx.workspace);
        let final_outcome = format!("{outcome}");
        let _ = session.write_summary(1, &final_outcome, ctx.workspace);
    }

    if let Some(result) = extraction_result {
        let message = result.into_message();
        return Ok(CommitAttemptResult {
            had_error,
            output_valid: true,
            message: Some(message),
            skip_reason: None,
            files,
            excluded_files: excluded,
            validation_detail: detail,
            auth_failure: false,
        });
    }

    Ok(CommitAttemptResult {
        had_error,
        output_valid: extraction_succeeded,
        message: None,
        skip_reason,
        files,
        excluded_files: excluded,
        validation_detail: detail,
        auth_failure: false,
    })
}
