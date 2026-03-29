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
    workspace_arc: &std::sync::Arc<dyn Workspace>,
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

    let base_cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    // Start MCP bridge so the agent can submit artifacts via ralph_submit_artifact
    let session = crate::agents::session::AgentSession::for_drain(
        unique_commit_plumbing_run_id("cps"),
        crate::agents::session::SessionDrain::Commit,
        1,
    );
    let harness_session = session.clone();
    let bridge = start_mcp_bridge(session, std::sync::Arc::clone(workspace_arc))
        .map_err(|e| anyhow::anyhow!(
            "MCP bridge startup failed for commit agent '{}': {}. MCP is mandatory and execution was aborted.",
            commit_agent,
            e
        ))?;
    let mcp_env: std::collections::HashMap<String, String> = std::collections::HashMap::from([(
        crate::mcp_server::session_bridge::MCP_ENDPOINT_ENV.to_string(),
        bridge.endpoint_uri(),
    )]);

    // Apply harness configuration if MCP bridge started successfully
    let agent_type = crate::agents::harness::applicator::detect_agent_type(&agent_config.cmd);
    let (harness_env, harness_extra_cmd_args) = if !mcp_env.is_empty() {
        let result = crate::agents::harness::applicator::apply_harness_config(
            agent_type,
            &harness_session,
            &bridge.endpoint_uri(),
            workspace,
        )
        .map_err(|e| anyhow::anyhow!(
            "MCP harness setup failed for commit agent '{}': {}. MCP is mandatory and execution was aborted.",
            commit_agent,
            e
        ))?;
        (result.extra_env_vars, result.extra_cmd_args)
    } else {
        (std::collections::HashMap::new(), Vec::new())
    };

    // Merge agent env vars with MCP env vars and harness env vars
    let merged_env: std::collections::HashMap<String, String> = agent_config
        .env_vars
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .chain(mcp_env)
        .chain(harness_env)
        .collect();

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
    let cmd_str = append_extra_cmd_args(&base_cmd_str, &harness_extra_cmd_args);
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
        env_vars: &merged_env,
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
    Fatal(anyhow::Error),
}

/// Contextual parameters for a single commit agent attempt.
struct CommitAgentContext<'a> {
    template_context: &'a TemplateContext,
    model_safe_diff: &'a str,
    registry: &'a AgentRegistry,
    workspace: &'a dyn Workspace,
    workspace_arc: &'a std::sync::Arc<dyn Workspace>,
}

fn try_single_commit_agent(
    agent_index: usize,
    commit_agent: &str,
    ctx: &CommitAgentContext<'_>,
    runtime: &mut PipelineRuntime<'_>,
) -> TryAgentResult {
    let template_context = ctx.template_context;
    let model_safe_diff = ctx.model_safe_diff;
    let registry = ctx.registry;
    let workspace = ctx.workspace;
    let workspace_arc = ctx.workspace_arc;
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
    let base_cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    // Start MCP bridge so the agent can submit artifacts via ralph_submit_artifact
    let session = crate::agents::session::AgentSession::for_drain(
        unique_commit_plumbing_run_id(&format!("cp{agent_index}")),
        crate::agents::session::SessionDrain::Commit,
        1,
    );
    let harness_session = session.clone();
    let bridge = match start_mcp_bridge(session, std::sync::Arc::clone(workspace_arc)) {
        Ok(b) => b,
        Err(e) => {
            return TryAgentResult::Fatal(anyhow::anyhow!(
                "MCP bridge startup failed for commit agent '{}': {}. MCP is mandatory and execution was aborted.",
                commit_agent,
                e
            ));
        }
    };
    let mcp_env: std::collections::HashMap<String, String> = std::collections::HashMap::from([(
        crate::mcp_server::session_bridge::MCP_ENDPOINT_ENV.to_string(),
        bridge.endpoint_uri(),
    )]);

    // Apply harness configuration if MCP bridge started successfully
    let agent_type = crate::agents::harness::applicator::detect_agent_type(&agent_config.cmd);
    let (harness_env, harness_extra_cmd_args) = if !mcp_env.is_empty() {
        match crate::agents::harness::applicator::apply_harness_config(
            agent_type,
            &harness_session,
            &bridge.endpoint_uri(),
            workspace,
        ) {
            Ok(result) => (result.extra_env_vars, result.extra_cmd_args),
            Err(e) => {
                return TryAgentResult::Fatal(anyhow::anyhow!(
                    "MCP harness setup failed for commit agent '{}': {}. MCP is mandatory and execution was aborted.",
                    commit_agent,
                    e
                ));
            }
        }
    } else {
        (std::collections::HashMap::new(), Vec::new())
    };

    // Merge agent env vars with MCP env vars and harness env vars
    let merged_env: std::collections::HashMap<String, String> = agent_config
        .env_vars
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .chain(mcp_env)
        .chain(harness_env)
        .collect();

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
    let cmd_str = append_extra_cmd_args(&base_cmd_str, &harness_extra_cmd_args);
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
        env_vars: &merged_env,
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
    workspace_arc: &std::sync::Arc<dyn Workspace>,
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

    use std::ops::ControlFlow;

    let fold_result = agents.iter().enumerate().try_fold(
        None::<anyhow::Error>,
        |last_err, (agent_index, commit_agent)| {
            let ctx = CommitAgentContext {
                template_context,
                model_safe_diff: &model_safe_diff,
                registry,
                workspace,
                workspace_arc,
            };
            match try_single_commit_agent(agent_index, commit_agent, &ctx, runtime) {
                TryAgentResult::Success(result) => ControlFlow::Break(Ok(result)),
                TryAgentResult::Skip(opt_err) => ControlFlow::Continue(opt_err.or(last_err)),
                TryAgentResult::Fatal(err) => ControlFlow::Break(Err(err)),
            }
        },
    );

    match fold_result {
        ControlFlow::Break(result) => result,
        ControlFlow::Continue(last_err) => {
            Err(last_err.unwrap_or_else(|| anyhow::anyhow!("All agents in commit chain failed")))
        }
    }
}

fn append_extra_cmd_args(cmd: &str, extra_args: &[String]) -> String {
    if extra_args.is_empty() {
        cmd.to_string()
    } else {
        let joined = extra_args.join(" ");
        format!("{cmd} {joined}")
    }
}

#[cfg(test)]
mod append_extra_cmd_args_tests {
    use super::*;

    #[test]
    fn append_extra_cmd_args_noop() {
        assert_eq!(append_extra_cmd_args("cmd", &[]), "cmd");
    }

    #[test]
    fn append_extra_cmd_args_appends_values() {
        let args = vec!["--settings".to_string(), "'/tmp/cache'".to_string()];
        assert_eq!(
            append_extra_cmd_args("claude", &args),
            "claude --settings '/tmp/cache'"
        );
    }
}
