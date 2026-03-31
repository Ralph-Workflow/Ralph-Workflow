/// Generate a commit message using a single agent attempt.
///
/// Returns an error if XML validation fails or the agent output is missing.
///
/// # Truncation Behavior (CLI vs Reducer)
///
/// **IMPORTANT:** This function uses **single-agent budget** for truncation, which
/// differs from the reducer-driven path that uses **chain-minimum budget**.
///
/// | Path | Budget Calculation | When Used |
/// |------|-------------------|-----------|
/// | CLI (`--generate-commit-msg`) | `model_budget_bytes_for_agent_name(agent)` | Single agent, no fallback chain |
/// | Reducer (`MaterializeCommitInputs`) | `effective_model_budget_bytes(&agents)` | Agent chain with potential fallbacks |
///
/// **Why this is acceptable:**
/// - CLI plumbing commands (`--generate-commit-msg`) invoke a single, explicitly-specified
///   agent with no fallback chain. There's no need to compute min budget across agents.
/// - The reducer path handles multi-agent chains where the diff must fit the smallest
///   agent's context window to ensure fallback attempts can succeed.
///
/// **Implication:** A diff that works via CLI might fail via reducer if the chain
/// includes an agent with a smaller budget. This is by design - the CLI user
/// explicitly chose the agent and accepts its budget constraints.
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn generate_commit_message(
    diff: &str,
    registry: &AgentRegistry,
    runtime: &mut PipelineRuntime<'_>,
    commit_agent: &str,
    template_context: &TemplateContext,
    workspace: &dyn Workspace,
) -> anyhow::Result<CommitMessageResult> {
    // For CLI plumbing, we truncate to the single agent's budget.
    // This is different from the reducer path which uses min budget across the chain.
    let model_budget = model_budget_bytes_for_agent_name(commit_agent);
    let (model_safe_diff, truncated) = truncate_diff_to_model_budget(diff, model_budget);
    if truncated {
        runtime.logger.warn(&format!(
            "Diff size ({} KB) exceeds agent limit ({} KB). Truncated to {} KB.",
            diff.len() / 1024,
            model_budget / 1024,
            model_safe_diff.len() / 1024
        ));
    }

    let (prompt, substitution_log) =
        build_commit_prompt(template_context, &model_safe_diff, workspace);
    if !substitution_log.is_complete() {
        return Err(anyhow::anyhow!(
            "Commit prompt has unresolved placeholders: {:?}",
            substitution_log.unsubstituted
        ));
    }

    let agent_config = registry
        .resolve_config(commit_agent)
        .ok_or_else(|| anyhow::anyhow!("Agent not found: {commit_agent}"))?;
    let cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    let log_prefix = ".agent/logs/commit_generation/commit_generation";
    let model_index = 0usize;
    let attempt = 1u32;
    let agent_for_log = commit_agent.to_lowercase();
    let logfile = crate::pipeline::logfile::build_logfile_path_with_attempt(
        log_prefix,
        &agent_for_log,
        model_index,
        attempt,
    );
    let prompt_cmd = PromptCommand {
        label: commit_agent,
        display_name: commit_agent,
        cmd_str: &cmd_str,
        prompt: &prompt,
        log_prefix,
        model_index: Some(model_index),
        attempt: Some(attempt),
        logfile: &logfile,
        parser_type: agent_config.json_parser,
        env_vars: &agent_config.env_vars,
        completion_output_path: Some(Path::new(xml_paths::COMMIT_MESSAGE_XML)),
    };

    let result = run_with_prompt(&prompt_cmd, runtime)?;
    let had_error = result.exit_code != 0;
    let auth_failure = had_error && stderr_contains_auth_error(&result.stderr);
    if auth_failure {
        anyhow::bail!("Authentication error detected");
    }

    let extraction = extract_commit_message_from_file_with_workspace(workspace);
    let result = match extraction {
        CommitExtractionOutcome::Valid {
            extracted: result,
            files: _,
            ..
        } => result,
        CommitExtractionOutcome::InvalidXml(detail)
        | CommitExtractionOutcome::MissingFile(detail) => anyhow::bail!(detail),
        CommitExtractionOutcome::Skipped(reason) => {
            archive_xml_file_with_workspace(workspace, Path::new(xml_paths::COMMIT_MESSAGE_XML));
            return Ok(CommitMessageResult {
                outcome: CommitMessageOutcome::Skipped { reason },
            });
        }
    };

    archive_xml_file_with_workspace(workspace, Path::new(xml_paths::COMMIT_MESSAGE_XML));

    Ok(CommitMessageResult {
        outcome: CommitMessageOutcome::Message(result.into_message()),
    })
}

/// Result of trying a single agent in the commit chain.
enum TryAgentResult {
    Success(CommitMessageResult),
    Skip(Option<anyhow::Error>),
}

fn try_single_commit_agent(
    agent_index: usize,
    commit_agent: &str,
    template_context: &TemplateContext,
    model_safe_diff: &str,
    registry: &AgentRegistry,
    runtime: &mut PipelineRuntime<'_>,
    workspace: &dyn Workspace,
) -> TryAgentResult {
    let (prompt, substitution_log) =
        build_commit_prompt(template_context, model_safe_diff, workspace);
    if !substitution_log.is_complete() {
        return TryAgentResult::Skip(Some(anyhow::anyhow!(
            "Commit prompt has unresolved placeholders: {:?}",
            substitution_log.unsubstituted
        )));
    }

    let Some(agent_config) = registry.resolve_config(commit_agent) else {
        return TryAgentResult::Skip(Some(anyhow::anyhow!("Agent not found: {commit_agent}")));
    };
    let cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    let log_prefix = ".agent/logs/commit_generation/commit_generation";
    let model_index = agent_index;
    let attempt = 1u32;
    let agent_for_log = commit_agent.to_lowercase();
    let logfile = crate::pipeline::logfile::build_logfile_path_with_attempt(
        log_prefix,
        &agent_for_log,
        model_index,
        attempt,
    );
    let prompt_cmd = PromptCommand {
        label: commit_agent,
        display_name: commit_agent,
        cmd_str: &cmd_str,
        prompt: &prompt,
        log_prefix,
        model_index: Some(model_index),
        attempt: Some(attempt),
        logfile: &logfile,
        parser_type: agent_config.json_parser,
        env_vars: &agent_config.env_vars,
        completion_output_path: Some(Path::new(xml_paths::COMMIT_MESSAGE_XML)),
    };

    let result = match run_with_prompt(&prompt_cmd, runtime) {
        Ok(r) => r,
        Err(e) => return TryAgentResult::Skip(Some(e.into())),
    };

    let had_error = result.exit_code != 0;
    let auth_failure = had_error && stderr_contains_auth_error(&result.stderr);

    if auth_failure {
        return TryAgentResult::Skip(Some(anyhow::anyhow!("Authentication error detected")));
    }

    if had_error {
        return TryAgentResult::Skip(Some(anyhow::anyhow!(
            "Agent {} failed with exit code {}",
            commit_agent,
            result.exit_code
        )));
    }

    let extraction = extract_commit_message_from_file_with_workspace(workspace);
    match extraction {
        CommitExtractionOutcome::Valid {
            extracted,
            files: _,
            ..
        } => {
            archive_xml_file_with_workspace(workspace, Path::new(xml_paths::COMMIT_MESSAGE_XML));
            TryAgentResult::Success(CommitMessageResult {
                outcome: CommitMessageOutcome::Message(extracted.into_message()),
            })
        }
        CommitExtractionOutcome::Skipped(reason) => {
            archive_xml_file_with_workspace(workspace, Path::new(xml_paths::COMMIT_MESSAGE_XML));
            TryAgentResult::Success(CommitMessageResult {
                outcome: CommitMessageOutcome::Skipped { reason },
            })
        }
        CommitExtractionOutcome::InvalidXml(detail)
        | CommitExtractionOutcome::MissingFile(detail) => {
            TryAgentResult::Skip(Some(anyhow::anyhow!(detail)))
        }
    }
}

/// Generate a commit message with fallback chain support.
///
/// Tries each agent in the chain sequentially until one succeeds.
/// Uses the minimum budget across all agents in the chain for truncation
/// to ensure the diff fits all potential fallback agents.
///
/// # Arguments
/// * `diff` - The diff to generate a commit message for
/// * `registry` - Agent registry for resolving agent configs
/// * `runtime` - Pipeline runtime for execution
/// * `agents` - Chain of agents to try in order (first agent tried first)
/// * `template_context` - Template context for prompt generation
/// * `workspace` - Workspace for file operations
/// # Returns
/// * `Ok(CommitMessageResult)` - If any agent in the chain succeeds
/// * `Err` - If all agents in the chain fail
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn generate_commit_message_with_chain(
    diff: &str,
    registry: &AgentRegistry,
    runtime: &mut PipelineRuntime<'_>,
    agents: &[String],
    template_context: &TemplateContext,
    workspace: &dyn Workspace,
) -> anyhow::Result<CommitMessageResult> {
    if agents.is_empty() {
        anyhow::bail!("No agents provided in commit chain");
    }

    // Use minimum budget across all agents in the chain
    let model_budget = effective_model_budget_bytes(agents);
    let (model_safe_diff, truncated) = truncate_diff_to_model_budget(diff, model_budget);
    if truncated {
        runtime.logger.warn(&format!(
            "Diff size ({} KB) exceeds chain limit ({} KB). Truncated to {} KB.",
            diff.len() / 1024,
            model_budget / 1024,
            model_safe_diff.len() / 1024
        ));
    }

    let last_error =
        agents
            .iter()
            .enumerate()
            .try_fold(
                None,
                |last_err, (agent_index, commit_agent)| match try_single_commit_agent(
                    agent_index,
                    commit_agent,
                    template_context,
                    &model_safe_diff,
                    registry,
                    runtime,
                    workspace,
                ) {
                    TryAgentResult::Success(result) => Err(result),
                    TryAgentResult::Skip(opt_err) => Ok(opt_err.or(last_err)),
                },
            );

    match last_error {
        Ok(last_err) => {
            Err(last_err.unwrap_or_else(|| anyhow::anyhow!("All agents in commit chain failed")))
        }
        Err(result) => Ok(result),
    }
}
