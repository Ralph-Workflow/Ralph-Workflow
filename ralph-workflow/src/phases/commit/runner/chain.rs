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
pub fn generate_commit_message<S: std::hash::BuildHasher + Default>(
    diff: &str,
    registry: &AgentRegistry,
    runtime: &mut PipelineRuntime<'_>,
    commit_agent: &str,
    template_context: &TemplateContext,
    workspace: &dyn Workspace,
    prompt_history: &HashMap<String, String, S>,
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

    // Prompt replay keys must be unique per commit cycle (diff/content) to prevent
    // reusing stale prompt text from prompt_history.
    let diff_id_sha256 = crate::reducer::prompt_inputs::sha256_hex_str(&model_safe_diff);
    let diff_id_short = diff_id_sha256.get(..12).unwrap_or(diff_id_sha256.as_str());
    let prompt_key = format!("commit_message_attempt_diff{diff_id_short}_attempt_1");
    let (prompt, was_replayed, substitution_log) = build_commit_prompt(
        &prompt_key,
        template_context,
        &model_safe_diff,
        workspace,
        prompt_history,
    );
    if let Some(log) = substitution_log {
        if !log.is_complete() {
            return Err(anyhow::anyhow!(
                "Commit prompt has unresolved placeholders: {:?}",
                log.unsubstituted
            ));
        }
    }

    let mut generated_prompts = HashMap::new();
    if !was_replayed {
        generated_prompts.insert(prompt_key, prompt.clone());
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
    };

    let result = run_with_prompt(&prompt_cmd, runtime)?;
    let had_error = result.exit_code != 0;
    let auth_failure = had_error && stderr_contains_auth_error(&result.stderr);
    if auth_failure {
        anyhow::bail!("Authentication error detected");
    }

    let extraction = extract_commit_message_from_file_with_workspace(workspace);
    let result = match extraction {
        CommitExtractionOutcome::Valid(result) => result,
        CommitExtractionOutcome::InvalidXml(detail)
        | CommitExtractionOutcome::MissingFile(detail) => anyhow::bail!(detail),
        CommitExtractionOutcome::Skipped(reason) => {
            archive_xml_file_with_workspace(workspace, Path::new(xml_paths::COMMIT_MESSAGE_XML));
            return Ok(CommitMessageResult {
                outcome: CommitMessageOutcome::Skipped { reason },
                generated_prompts,
            });
        }
    };

    archive_xml_file_with_workspace(workspace, Path::new(xml_paths::COMMIT_MESSAGE_XML));

    Ok(CommitMessageResult {
        outcome: CommitMessageOutcome::Message(result.into_message()),
        generated_prompts,
    })
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
/// * `prompt_history` - History of prompts for replay detection
///
/// # Returns
/// * `Ok(CommitMessageResult)` - If any agent in the chain succeeds
/// * `Err` - If all agents in the chain fail
///
/// # Errors
///
/// Returns error if the operation fails.
pub fn generate_commit_message_with_chain<S: std::hash::BuildHasher + Default>(
    diff: &str,
    registry: &AgentRegistry,
    runtime: &mut PipelineRuntime<'_>,
    agents: &[String],
    template_context: &TemplateContext,
    workspace: &dyn Workspace,
    prompt_history: &HashMap<String, String, S>,
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

    let mut last_error: Option<anyhow::Error> = None;
    let mut generated_prompts = HashMap::new();

    let diff_id_sha256 = crate::reducer::prompt_inputs::sha256_hex_str(&model_safe_diff);
    let diff_id_short = diff_id_sha256.get(..12).unwrap_or(diff_id_sha256.as_str());

    for (agent_index, commit_agent) in agents.iter().enumerate() {
        let prompt_key = format!(
            "commit_message_chain_diff{diff_id_short}_attempt_{}",
            agent_index + 1
        );
        let (prompt, was_replayed, substitution_log) = build_commit_prompt(
            &prompt_key,
            template_context,
            &model_safe_diff,
            workspace,
            prompt_history,
        );
        if let Some(log) = substitution_log {
            if !log.is_complete() {
                return Err(anyhow::anyhow!(
                    "Commit prompt has unresolved placeholders: {:?}",
                    log.unsubstituted
                ));
            }
        }

        if !was_replayed {
            generated_prompts.insert(prompt_key.clone(), prompt.clone());
        }

        let Some(agent_config) = registry.resolve_config(commit_agent) else {
            last_error = Some(anyhow::anyhow!("Agent not found: {commit_agent}"));
            continue;
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
        };

        let result = match run_with_prompt(&prompt_cmd, runtime) {
            Ok(r) => r,
            Err(e) => {
                last_error = Some(e.into());
                continue;
            }
        };

        let had_error = result.exit_code != 0;
        let auth_failure = had_error && stderr_contains_auth_error(&result.stderr);

        if auth_failure {
            last_error = Some(anyhow::anyhow!("Authentication error detected"));
            continue;
        }

        if had_error {
            last_error = Some(anyhow::anyhow!(
                "Agent {} failed with exit code {}",
                commit_agent,
                result.exit_code
            ));
            continue;
        }

        let extraction = extract_commit_message_from_file_with_workspace(workspace);
        match extraction {
            CommitExtractionOutcome::Valid(extracted) => {
                archive_xml_file_with_workspace(
                    workspace,
                    Path::new(xml_paths::COMMIT_MESSAGE_XML),
                );
                return Ok(CommitMessageResult {
                    outcome: CommitMessageOutcome::Message(extracted.into_message()),
                    generated_prompts,
                });
            }
            CommitExtractionOutcome::Skipped(reason) => {
                archive_xml_file_with_workspace(
                    workspace,
                    Path::new(xml_paths::COMMIT_MESSAGE_XML),
                );
                return Ok(CommitMessageResult {
                    outcome: CommitMessageOutcome::Skipped { reason },
                    generated_prompts,
                });
            }
            CommitExtractionOutcome::InvalidXml(detail)
            | CommitExtractionOutcome::MissingFile(detail) => {
                last_error = Some(anyhow::anyhow!(detail));
            }
        }
    }

    // All agents failed
    Err(last_error.unwrap_or_else(|| anyhow::anyhow!("All agents in commit chain failed")))
}
