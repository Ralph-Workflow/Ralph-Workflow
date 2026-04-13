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

    let agent_config = registry
        .resolve_config(commit_agent)
        .ok_or_else(|| anyhow::anyhow!("Agent not found: {commit_agent}"))?;
    let agent_type = crate::agents::harness::applicator::detect_agent_type(&agent_config.cmd);

    let (raw_prompt, substitution_log) =
        build_commit_prompt(template_context, &model_safe_diff, workspace);
    if !substitution_log.is_complete() {
        return Err(anyhow::anyhow!(
            "Commit prompt has unresolved placeholders: {:?}",
            substitution_log.unsubstituted
        ));
    }

    let base_cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    let mcp = start_commit_mcp_context(unique_commit_plumbing_run_id("cps"), workspace_arc)?;
    let harness_session = mcp.session.clone();
    let mcp_env = build_commit_mcp_env(
        crate::mcp_server::session_bridge::MCP_ENDPOINT_ENV,
        &mcp.endpoint_uri,
        commit_agent,
        mcp.endpoint_lease.as_ref(),
    )?;

    let result = crate::agents::harness::applicator::apply_harness_config_with_lease(
        agent_type,
        &harness_session,
        &mcp.endpoint_uri,
        workspace,
        mcp.endpoint_lease.as_ref(),
    )
    .map_err(|e| anyhow::anyhow!(
        "MCP harness setup failed for commit agent '{}': {}. MCP is mandatory and execution was aborted.",
        commit_agent,
        e
    ))?;
    let (harness_env, harness_extra_cmd_args) = (result.extra_env_vars, result.extra_cmd_args);

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
    let cmd_str = append_extra_cmd_args(agent_type, &base_cmd_str, &harness_extra_cmd_args);
    let prompt = crate::agents::tool_manifest::rewrite_prompt_mcp_tool_names(
        &raw_prompt,
        harness_session.capabilities(),
        matches!(
            agent_type,
            crate::agents::harness::applicator::AgentType::Claude
        ),
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
        env_vars: &merged_env,
        completion_output_path: Some(Path::new(artifact_paths::COMMIT_MESSAGE_JSON)),
    };

    let result = run_with_prompt(&prompt_cmd, runtime)?;
    let had_error = result.exit_code != 0;
    let auth_failure = had_error && stderr_contains_auth_error(&result.stderr);
    if auth_failure {
        anyhow::bail!("Authentication error detected");
    }
    if (commit_submit_tool_unavailable(&result.stderr)
        || log_indicates_submit_tool_unavailable(workspace, &logfile))
        && !recover_commit_artifact_from_log(workspace, &logfile)
    {
        anyhow::bail!(
            "Commit submission tool is unavailable for agent '{}': output did not expose 'ralph_submit_artifact'",
            commit_agent
        );
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
            crate::files::archive_json_artifact_with_workspace(workspace, "commit_message");
            return Ok(CommitMessageResult {
                outcome: CommitMessageOutcome::Skipped { reason },
            });
        }
    };

    crate::files::archive_json_artifact_with_workspace(workspace, "commit_message");

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
    mcp: &'a CommitMcpContext,
}

struct CommitMcpContext {
    _bridge: crate::mcp_server::session_bridge::SessionBridge,
    session: crate::agents::session::AgentSession,
    endpoint_uri: String,
    endpoint_lease: Option<mcp_server::io::EndpointLease>,
}

fn start_commit_mcp_context(
    run_id: String,
    workspace_arc: &std::sync::Arc<dyn Workspace>,
) -> anyhow::Result<CommitMcpContext> {
    let session = crate::agents::session::AgentSession::for_drain(
        run_id,
        crate::agents::session::SessionDrain::Commit,
        1,
    );
    let bridge = start_mcp_bridge(session.clone(), std::sync::Arc::clone(workspace_arc)).map_err(
        |e| anyhow::anyhow!(
            "MCP bridge startup failed for commit run '{}': {}. MCP is mandatory and execution was aborted.",
            session.run_id,
            e
        ),
    )?;
    let endpoint_lease = bridge.endpoint_lease();
    if let Some(lease) = endpoint_lease.as_ref() {
        if let Err(error) =
            crate::agents::session::audit::persist_endpoint_lease(workspace_arc.as_ref(), lease)
        {
            eprintln!(
                "warning: failed to persist MCP endpoint lease for commit run '{}': {}",
                session.run_id, error
            );
        }
    }
    Ok(CommitMcpContext {
        endpoint_uri: bridge.agent_endpoint_uri(),
        endpoint_lease,
        _bridge: bridge,
        session,
    })
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
    let mcp = ctx.mcp;
    let Some(agent_config) = registry.resolve_config(commit_agent) else {
        return TryAgentResult::Skip(Some(anyhow::anyhow!("Agent not found: {commit_agent}")));
    };
    let agent_type = crate::agents::harness::applicator::detect_agent_type(&agent_config.cmd);
    let (raw_prompt, substitution_log) =
        build_commit_prompt(template_context, model_safe_diff, workspace);
    if !substitution_log.is_complete() {
        return TryAgentResult::Skip(Some(anyhow::anyhow!(
            "Commit prompt has unresolved placeholders: {:?}",
            substitution_log.unsubstituted
        )));
    }
    let base_cmd_str = agent_config.build_cmd_with_model(true, true, true, None);

    let harness_session = mcp.session.clone();
    let mcp_env = match build_commit_mcp_env(
        crate::mcp_server::session_bridge::MCP_ENDPOINT_ENV,
        &mcp.endpoint_uri,
        commit_agent,
        mcp.endpoint_lease.as_ref(),
    ) {
        Ok(env) => env,
        Err(err) => return TryAgentResult::Fatal(err),
    };

    let (harness_env, harness_extra_cmd_args) =
        match crate::agents::harness::applicator::apply_harness_config_with_lease(
            agent_type,
            &harness_session,
            &mcp.endpoint_uri,
            workspace,
            mcp.endpoint_lease.as_ref(),
        ) {
            Ok(result) => (result.extra_env_vars, result.extra_cmd_args),
            Err(e) => {
                return TryAgentResult::Skip(Some(anyhow::anyhow!(
                    "MCP harness setup failed for commit agent '{}': {}. Skipping to next agent in chain.",
                    commit_agent,
                    e
                )));
            }
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
    let cmd_str = append_extra_cmd_args(agent_type, &base_cmd_str, &harness_extra_cmd_args);
    let prompt = crate::agents::tool_manifest::rewrite_prompt_mcp_tool_names(
        &raw_prompt,
        harness_session.capabilities(),
        matches!(
            agent_type,
            crate::agents::harness::applicator::AgentType::Claude
        ),
    );
    let submit_tool_name = if matches!(
        agent_type,
        crate::agents::harness::applicator::AgentType::Claude
    ) {
        "mcp__ralph__ralph_submit_artifact"
    } else {
        "ralph_submit_artifact"
    };
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
        completion_output_path: Some(Path::new(artifact_paths::COMMIT_MESSAGE_JSON)),
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
    if (commit_submit_tool_unavailable(&result.stderr)
        || log_indicates_submit_tool_unavailable(workspace, &logfile))
        && !recover_commit_artifact_from_log(workspace, &logfile)
    {
        return TryAgentResult::Skip(Some(anyhow::anyhow!(
            "Commit submission tool unavailable for agent '{}'; skipping retry loop",
            commit_agent
        )));
    }

    if had_error && !crate::files::has_valid_artifact_output(workspace, Path::new(artifact_paths::COMMIT_MESSAGE_JSON)) {
        let retry_prompt = commit_submission_retry_prompt(&prompt, submit_tool_name);
        let retry_cmd = PromptCommand {
            label: commit_agent,
            display_name: commit_agent,
            cmd_str: &cmd_str,
            prompt: &retry_prompt,
            log_prefix,
            model_index: Some(model_index),
            attempt: Some(attempt),
            logfile: &logfile,
            parser_type: agent_config.json_parser,
            env_vars: &merged_env,
            completion_output_path: Some(Path::new(artifact_paths::COMMIT_MESSAGE_JSON)),
        };
        let retry_result = match run_with_prompt(&retry_cmd, runtime) {
            Ok(r) => r,
            Err(e) => return TryAgentResult::Skip(Some(e.into())),
        };
        if retry_result.exit_code != 0
            && !crate::files::has_valid_artifact_output(workspace, Path::new(artifact_paths::COMMIT_MESSAGE_JSON))
        {
            return TryAgentResult::Skip(Some(anyhow::anyhow!(
                "Agent {} failed with exit code {} after submission retry",
                commit_agent,
                retry_result.exit_code
            )));
        }
    }

    let extraction = extract_commit_message_from_file_with_workspace(workspace);
    match extraction {
        CommitExtractionOutcome::Valid {
            extracted,
            files: _,
            ..
        } => {
crate::files::archive_json_artifact_with_workspace(workspace, "commit_message");
            TryAgentResult::Success(CommitMessageResult {
                outcome: CommitMessageOutcome::Message(extracted.into_message()),
            })
        }
        CommitExtractionOutcome::Skipped(reason) => {
crate::files::archive_json_artifact_with_workspace(workspace, "commit_message");
            TryAgentResult::Success(CommitMessageResult {
                outcome: CommitMessageOutcome::Skipped { reason },
            })
        }
        CommitExtractionOutcome::InvalidXml(detail)
        | CommitExtractionOutcome::MissingFile(detail) => {
            let retry_prompt = commit_submission_retry_prompt(&prompt, submit_tool_name);
            let retry_cmd = PromptCommand {
                label: commit_agent,
                display_name: commit_agent,
                cmd_str: &cmd_str,
                prompt: &retry_prompt,
                log_prefix,
                model_index: Some(model_index),
                attempt: Some(attempt),
                logfile: &logfile,
                parser_type: agent_config.json_parser,
                env_vars: &merged_env,
                completion_output_path: Some(Path::new(artifact_paths::COMMIT_MESSAGE_JSON)),
            };
            match run_with_prompt(&retry_cmd, runtime) {
                Ok(_) => match extract_commit_message_from_file_with_workspace(workspace) {
                    CommitExtractionOutcome::Valid {
                        extracted,
                        files: _,
                        ..
                    } => {
                        crate::files::archive_json_artifact_with_workspace(workspace, "commit_message");
                        TryAgentResult::Success(CommitMessageResult {
                            outcome: CommitMessageOutcome::Message(extracted.into_message()),
                        })
                    }
                    CommitExtractionOutcome::Skipped(reason) => {
                        crate::files::archive_json_artifact_with_workspace(workspace, "commit_message");
                        TryAgentResult::Success(CommitMessageResult {
                            outcome: CommitMessageOutcome::Skipped { reason },
                        })
                    }
                    CommitExtractionOutcome::InvalidXml(retry_detail)
                    | CommitExtractionOutcome::MissingFile(retry_detail) => {
                        TryAgentResult::Skip(Some(anyhow::anyhow!(
                            "{detail}; submission retry also failed: {retry_detail}"
                        )))
                    }
                },
                Err(err) => TryAgentResult::Skip(Some(err.into())),
            }
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

    let mcp = start_commit_mcp_context(unique_commit_plumbing_run_id("cp"), workspace_arc)?;

    let fold_result = agents.iter().enumerate().try_fold(
        None::<anyhow::Error>,
        |last_err, (agent_index, commit_agent)| {
            let ctx = CommitAgentContext {
                template_context,
                model_safe_diff: &model_safe_diff,
                registry,
                workspace,
                mcp: &mcp,
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

fn append_extra_cmd_args(
    agent_type: crate::agents::harness::applicator::AgentType,
    cmd: &str,
    extra_args: &[String],
) -> String {
    crate::agents::command_line::append_agent_command_args(
        cmd,
        extra_args,
        matches!(
            agent_type,
            crate::agents::harness::applicator::AgentType::Claude
        ),
    )
}

fn build_commit_mcp_env(
    endpoint_env_var: &str,
    endpoint_uri: &str,
    commit_agent: &str,
    lease: Option<&mcp_server::io::EndpointLease>,
) -> anyhow::Result<std::collections::HashMap<String, String>> {
    if endpoint_uri.is_empty() {
        Err(anyhow::anyhow!(
            "MCP endpoint missing for commit agent '{}'. MCP is mandatory and execution was aborted.",
            commit_agent
        ))
    } else if !(endpoint_uri.starts_with("tcp://") || endpoint_uri.starts_with("http://")) {
        Err(anyhow::anyhow!(
            "MCP endpoint for commit agent '{}' must be tcp:// or http://, got '{}'. MCP is mandatory and execution was aborted.",
            commit_agent,
            endpoint_uri
        ))
    } else {
        Ok(
            std::iter::once((endpoint_env_var.to_string(), endpoint_uri.to_string()))
                .chain(lease.into_iter().flat_map(|lease| {
                    [
                        (
                            crate::mcp_server::session_bridge::MCP_GENERATION_ENV.to_string(),
                            lease.generation.to_string(),
                        ),
                        (
                            crate::mcp_server::session_bridge::MCP_RUN_ID_ENV.to_string(),
                            lease.run_id.clone(),
                        ),
                    ]
                }))
                .collect(),
        )
    }
}

fn commit_submit_tool_unavailable(stderr: &str) -> bool {
    let stderr_lower = stderr.to_lowercase();
    stderr_lower.contains("ralph_submit_artifact")
        && (stderr_lower.contains("unavailable tool")
            || stderr_lower.contains("don't have a tool")
            || stderr_lower.contains("mcp tools are not available")
            || (stderr_lower.contains("tool: invalid")
                && stderr_lower.contains("tool=ralph_submit_artifact")))
}

fn log_indicates_submit_tool_unavailable(workspace: &dyn Workspace, logfile: &str) -> bool {
    let Ok(content) = workspace.read(Path::new(logfile)) else {
        return false;
    };
    let lower = content.to_lowercase();
    lower.contains("ralph_submit_artifact")
        && (lower.contains("unavailable tool")
            || lower.contains("don't have a tool")
            || lower.contains("mcp tools are not available")
            || (lower.contains("tool: invalid") && lower.contains("tool=ralph_submit_artifact")))
}

fn commit_payload_json(value: &serde_json::Value) -> bool {
    matches!(
        value.get("type").and_then(|v| v.as_str()),
        Some("commit") | Some("skip")
    )
}

fn extract_commit_payload_from_log(content: &str) -> Option<serde_json::Value> {
    content
        .split("```json")
        .skip(1)
        .filter_map(|segment| {
            segment
                .split_once("```")
                .map(|(json_block, _rest)| json_block)
        })
        .map(str::trim)
        .filter_map(|trimmed| serde_json::from_str::<serde_json::Value>(trimmed).ok())
        .chain(
            content
                .char_indices()
                .filter(|(_, ch)| *ch == '{')
                .filter_map(|(idx, _)| {
                    serde_json::Deserializer::from_str(&content[idx..])
                        .into_iter::<serde_json::Value>()
                        .next()
                        .and_then(Result::ok)
                }),
        )
        .filter(commit_payload_json)
        .last()
}

fn recover_commit_artifact_from_log(workspace: &dyn Workspace, logfile: &str) -> bool {
    if matches!(workspace.read_artifact_json("commit_message"), Ok(Some(_))) {
        return true;
    }
    let Ok(content) = workspace.read(Path::new(logfile)) else {
        return false;
    };
    let Some(payload) = extract_commit_payload_from_log(&content) else {
        return false;
    };
    let envelope = crate::workspace::ArtifactEnvelope::new(
        "commit_message",
        payload,
        chrono::Utc::now().to_rfc3339(),
    );
    workspace.write_artifact_json(&envelope).is_ok()
}

#[cfg(test)]
mod append_extra_cmd_args_tests {
    use super::*;
    use crate::mcp_server::session_bridge::{MCP_GENERATION_ENV, MCP_RUN_ID_ENV};
    use crate::workspace::memory_workspace::MemoryWorkspace;
    use crate::workspace::WorkspaceFs;
    use mcp_server::io::EndpointLease;
    use std::sync::Arc;

    #[test]
    fn append_extra_cmd_args_noop() {
        assert_eq!(
            append_extra_cmd_args(
                crate::agents::harness::applicator::AgentType::Unknown,
                "cmd",
                &[]
            ),
            "cmd"
        );
    }

    #[test]
    fn append_extra_cmd_args_appends_values() {
        let args = vec!["--settings".to_string(), "'/tmp/cache'".to_string()];
        assert_eq!(
            append_extra_cmd_args(
                crate::agents::harness::applicator::AgentType::Unknown,
                "claude",
                &args
            ),
            "claude --settings '/tmp/cache'"
        );
    }

    #[test]
    fn append_extra_cmd_args_replaces_existing_claude_harness_flags() {
        let args = vec![
            "--settings".to_string(),
            "'/tmp/new-settings'".to_string(),
            "--mcp-config".to_string(),
            "'/tmp/new-mcp'".to_string(),
            "--strict-mcp-config".to_string(),
        ];
        let existing =
            "claude --settings '/tmp/old-settings' --mcp-config '/tmp/old-mcp' --strict-mcp-config";

        let merged = append_extra_cmd_args(
            crate::agents::harness::applicator::AgentType::Claude,
            existing,
            &args,
        );

        assert!(
            !merged.contains("/tmp/old-settings"),
            "existing --settings must be removed: {merged}"
        );
        assert!(
            !merged.contains("/tmp/old-mcp"),
            "existing --mcp-config must be removed: {merged}"
        );
        assert!(
            merged.contains("/tmp/new-settings") && merged.contains("/tmp/new-mcp"),
            "new harness args must be appended: {merged}"
        );
    }

    #[test]
    fn build_commit_mcp_env_rejects_empty_endpoint() {
        let err = build_commit_mcp_env("RALPH_MCP_ENDPOINT", "", "commit-agent", None)
            .expect_err("empty endpoint must fail closed");
        assert!(
            err.to_string()
                .contains("MCP endpoint missing for commit agent 'commit-agent'"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn build_commit_mcp_env_builds_endpoint_env() {
        let env = build_commit_mcp_env(
            "RALPH_MCP_ENDPOINT",
            "tcp://127.0.0.1:47001",
            "commit-agent",
            None,
        )
        .expect("non-empty endpoint should build env");
        assert_eq!(
            env.get("RALPH_MCP_ENDPOINT").map(String::as_str),
            Some("tcp://127.0.0.1:47001")
        );
    }

    #[test]
    fn build_commit_mcp_env_accepts_http_endpoint() {
        let env = build_commit_mcp_env(
            "RALPH_MCP_ENDPOINT",
            "http://127.0.0.1:60161/mcp",
            "commit-agent",
            None,
        )
        .expect("http endpoint should be accepted");
        assert_eq!(
            env.get("RALPH_MCP_ENDPOINT").map(String::as_str),
            Some("http://127.0.0.1:60161/mcp")
        );
    }

    #[test]
    fn build_commit_mcp_env_includes_generation_and_run_id_from_lease() {
        let lease = EndpointLease::new(
            "tcp://127.0.0.1:47001".into(),
            "run-commit-123".into(),
            7,
            std::time::SystemTime::UNIX_EPOCH,
        );
        let env = build_commit_mcp_env(
            "RALPH_MCP_ENDPOINT",
            "tcp://127.0.0.1:47001",
            "commit-agent",
            Some(&lease),
        )
        .expect("lease metadata must be threaded into commit plumbing env");

        assert_eq!(env.get(MCP_GENERATION_ENV).map(String::as_str), Some("7"));
        assert_eq!(
            env.get(MCP_RUN_ID_ENV).map(String::as_str),
            Some("run-commit-123")
        );
    }

    #[test]
    fn build_commit_mcp_env_rejects_unix_endpoint() {
        let err = build_commit_mcp_env(
            "RALPH_MCP_ENDPOINT",
            "unix:///tmp/ralph.sock",
            "commit-agent",
            None,
        )
        .expect_err("unix endpoint must be rejected");
        assert!(
            err.to_string().contains("must be tcp:// or http://"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn commit_submit_tool_unavailable_detects_unavailable_submit_artifact() {
        let stderr = "Model tried to call unavailable tool 'ralph_submit_artifact'.";
        assert!(commit_submit_tool_unavailable(stderr));
    }

    #[test]
    fn commit_submit_tool_unavailable_detects_invalid_tool_wrapper_signal() {
        let stderr = "Tool: invalid; tool=ralph_submit_artifact";
        assert!(commit_submit_tool_unavailable(stderr));
    }

    #[test]
    fn commit_submit_tool_unavailable_ignores_unrelated_output() {
        assert!(!commit_submit_tool_unavailable(
            "Generated commit message successfully"
        ));
    }

    #[test]
    fn log_indicates_submit_tool_unavailable_detects_pattern() {
        let workspace = MemoryWorkspace::new_test();
        let log = ".agent/logs/commit_generation/test_tool_unavailable.log";
        workspace
            .create_dir_all(std::path::Path::new(".agent/logs/commit_generation"))
            .expect("create log dir");
        workspace
            .write(
                std::path::Path::new(log),
                "Model tried to call unavailable tool 'ralph_submit_artifact'",
            )
            .expect("write log");

        assert!(log_indicates_submit_tool_unavailable(&workspace, log));
    }

    #[test]
    fn log_indicates_submit_tool_unavailable_ignores_missing_log() {
        let workspace = MemoryWorkspace::new_test();
        assert!(!log_indicates_submit_tool_unavailable(
            &workspace,
            ".agent/logs/commit_generation/absent.log"
        ));
    }

    #[test]
    fn extract_commit_payload_from_log_reads_latest_json_block() {
        let content = r#"
before
```json
{"type":"commit","subject":"feat: first"}
```
after
```json
{"type":"commit","subject":"feat: latest","body_summary":"summary"}
```
"#;
        let payload = extract_commit_payload_from_log(content).expect("payload expected");
        assert_eq!(payload.get("type").and_then(|v| v.as_str()), Some("commit"));
        assert_eq!(
            payload.get("subject").and_then(|v| v.as_str()),
            Some("feat: latest")
        );
    }

    #[test]
    fn recover_commit_artifact_from_log_persists_commit_json() {
        let workspace = MemoryWorkspace::new_test();
        let log = ".agent/logs/commit_generation/recover_from_log.log";
        workspace
            .create_dir_all(std::path::Path::new(".agent/logs/commit_generation"))
            .expect("create log dir");
        workspace
            .write(
                std::path::Path::new(log),
                r#"assistant output
```json
{"type":"commit","subject":"fix: recovered from log","body_summary":"Recovered"}
```
"#,
            )
            .expect("write log");

        assert!(recover_commit_artifact_from_log(&workspace, log));
        let envelope = workspace
            .read_artifact_json("commit_message")
            .expect("read artifact")
            .expect("artifact should exist");
        assert_eq!(
            envelope.content.get("subject").and_then(|v| v.as_str()),
            Some("fix: recovered from log")
        );
    }

    #[test]
    fn extract_commit_payload_from_log_reads_plain_json_block_without_fence() {
        let content = r#"
analysis text before
{
  "type": "commit",
  "subject": "feat: plain-json",
  "body_summary": "no fence"
}
analysis text after
"#;
        let payload = extract_commit_payload_from_log(content).expect("payload expected");
        assert_eq!(payload.get("type").and_then(|v| v.as_str()), Some("commit"));
        assert_eq!(
            payload.get("subject").and_then(|v| v.as_str()),
            Some("feat: plain-json")
        );
    }

    #[test]
    fn start_commit_mcp_context_builds_commit_session_and_endpoint() {
        let root = std::path::PathBuf::from("/");
        let workspace: Arc<dyn Workspace> = Arc::new(WorkspaceFs::new(root));

        let ctx = start_commit_mcp_context("commit-run".to_string(), &workspace)
            .expect("shared commit MCP context must start");

        assert_eq!(ctx.session.run_id, "commit-run");
        assert_eq!(ctx.session.drain.as_str(), "commit");
        assert!(!ctx.endpoint_uri.is_empty());
        assert!(
            ctx.endpoint_uri.starts_with("http://127.0.0.1:") && ctx.endpoint_uri.ends_with("/mcp"),
            "unexpected endpoint URI: {}",
            ctx.endpoint_uri
        );
    }
}
