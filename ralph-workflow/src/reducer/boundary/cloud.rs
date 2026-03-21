//! Cloud mode effect handlers.
//!
//! This module implements effect handlers for cloud-specific operations:
//! - Git authentication configuration
//! - Remote push operations
//! - Pull request creation
//! - Progress reporting
//!
//! All handlers follow the reducer architecture contract:
//! - Execute a single operation
//! - Emit events describing outcomes
//! - No retry logic (reducer decides)

use super::io_cloud::is_success;
use super::MainEffectHandler;
use crate::phases::PhaseContext;
use crate::reducer::effect::EffectResult;
use crate::reducer::event::{CommitEvent, PipelineEvent};
use crate::reducer::ui_event::UIEvent;

impl MainEffectHandler {
    /// Configure git authentication for remote operations.
    ///
    /// This handler sets up git credentials based on the auth method:
    /// - SSH key: Configure `GIT_SSH_COMMAND` environment variable
    /// - Token: Set up git credential helper
    /// - Credential helper: Configure external helper
    pub(super) fn handle_configure_git_auth(
        ctx: &PhaseContext<'_>,
        auth_method: &str,
    ) -> EffectResult {
        ctx.logger
            .info(&format!("Configuring git authentication: {auth_method}"));

        // Parse auth method string (format: "method:param")
        let parts: Vec<&str> = auth_method.splitn(2, ':').collect();
        let method = parts.first().unwrap_or(&"ssh-key");
        let param = parts.get(1).unwrap_or(&"default");

        match *method {
            "ssh-key" => {
                // Configure SSH key authentication
                if *param == "default" {
                    // Use default SSH key (SSH_AUTH_SOCK or ~/.ssh/id_rsa)
                    ctx.logger
                        .info("Using default SSH authentication (SSH_AUTH_SOCK or ~/.ssh/id_rsa)");
                } else {
                    // Configure GIT_SSH_COMMAND to use specific key via the git environment.
                    // Git may execute this via a shell; treat the key path as untrusted.
                    if let Ok(()) = ctx.env.configure_git_ssh_command(param) {
                        ctx.logger
                            .info("Set GIT_SSH_COMMAND to use provided SSH key");
                    } else {
                        ctx.logger.warn(
                            "Invalid SSH key path for cloud git auth; falling back to default SSH",
                        );
                    }
                }
            }
            "token" => {
                // Configure token-based authentication.
                // We intentionally do NOT embed or log the token.
                // Push operations use a non-persistent credential helper that reads the token
                // from environment variables at runtime.
                ctx.logger.info(&format!(
                    "Configuring token authentication for user: {param}"
                ));
                let _ = ctx.env.disable_git_terminal_prompt();
            }
            "credential-helper" => {
                // Configure external credential helper
                ctx.logger
                    .info(&format!("Using credential helper: {param}"));
                let _ = ctx.env.disable_git_terminal_prompt();
            }
            _ => {
                ctx.logger.warn(&format!(
                    "Unknown auth method: {method}, falling back to default SSH"
                ));
            }
        }

        EffectResult::event(PipelineEvent::Commit(CommitEvent::GitAuthConfigured))
    }

    /// Push commits to remote repository.
    ///
    /// Executes git push command and emits domain-shaped execution outcome.
    /// The reducer/orchestrator interprets the outcome and applies policy-level success/failure.
    pub(super) fn handle_push_to_remote(
        ctx: &PhaseContext<'_>,
        remote: String,
        branch: String,
        force: bool,
        commit_sha: String,
    ) -> EffectResult {
        ctx.logger.info(&format!(
            "Pushing commit {} to {}/{}{}",
            &commit_sha[..7.min(commit_sha.len())],
            remote,
            branch,
            if force { " (force)" } else { "" }
        ));

        // Build git push command.
        // Auth is configured in a checkpoint-safe way:
        // - ssh-key: via GIT_SSH_COMMAND (set in ConfigureGitAuth)
        // - token: via ephemeral credential helper that reads token from env
        // - credential-helper: via per-command credential.helper override
        let Some(refspec) = build_head_push_refspec(&branch) else {
            let error = crate::cloud::redaction::redact_secrets(&format!(
                "Invalid push branch name: '{branch}'"
            ));
            ctx.logger.warn(&format!("Git push skipped: {error}"));

            let ui = UIEvent::PushFailed {
                remote: remote.clone(),
                branch: branch.clone(),
                error: error.clone(),
            };

            return EffectResult::with_ui(
                PipelineEvent::Commit(CommitEvent::PushFailed {
                    remote,
                    branch,
                    error,
                }),
                vec![ui],
            );
        };

        let argv: Vec<String> = match &ctx.cloud.git_remote.auth_method {
            crate::config::types::GitAuthMethod::SshKey { .. } => vec![],
            crate::config::types::GitAuthMethod::Token { .. } => vec![
                "-c".to_string(),
                "credential.helper=!f() { echo username=$RALPH_GIT_TOKEN_USERNAME; echo password=$RALPH_GIT_TOKEN; }; f"
                    .to_string(),
                "-c".to_string(),
                "credential.useHttpPath=true".to_string(),
            ],
            crate::config::types::GitAuthMethod::CredentialHelper { helper } => vec![
                "-c".to_string(),
                format!("credential.helper={helper}"),
                "-c".to_string(),
                "credential.useHttpPath=true".to_string(),
            ],
        }
        .into_iter()
        .chain(std::iter::once("push".to_string()))
        .chain(std::iter::once(remote.clone()))
        .chain(std::iter::once(refspec))
        .chain(force.then(|| "--force".to_string()))
        .collect();

        let git_args: Vec<&str> = argv.iter().map(std::string::String::as_str).collect();

        // Execute push via executor
        let result = ctx
            .executor
            .execute("git", &git_args, &[], Some(ctx.repo_root));

        match result {
            Ok(output) => {
                // Boundary emits domain-shaped outcome with raw process result.
                // Reducer interprets the result and applies policy-level state transitions.
                let exit_code = output.status.code().unwrap_or(-1);
                let success = output.status.success();
                let stderr = output.stderr.clone();

                ctx.logger.info(&format!(
                    "Git push executed for {remote}/{branch} (exit: {exit_code})"
                ));

                let exec_result: crate::reducer::event::ProcessExecutionResult = output.into();

                let primary_event = PipelineEvent::Commit(CommitEvent::PushExecuted {
                    remote: remote.clone(),
                    branch: branch.clone(),
                    commit_sha: commit_sha.clone(),
                    result: exec_result,
                });

                // Boundary attaches UI events based on exit code interpretation.
                // Policy-level state transitions happen in the reducer.
                if success {
                    ctx.logger
                        .info(&format!("Successfully pushed to {remote}/{branch}"));

                    let ui = UIEvent::PushCompleted {
                        remote,
                        branch,
                        commit_sha,
                    };

                    EffectResult::event(primary_event).with_ui_event(ui)
                } else {
                    let error = crate::cloud::redaction::redact_secrets(&stderr);
                    ctx.logger.warn(&format!("Git push failed: {error}"));

                    let ui = UIEvent::PushFailed {
                        remote,
                        branch,
                        error,
                    };

                    EffectResult::event(primary_event).with_ui_event(ui)
                }
            }
            Err(e) => {
                // Executor-level failure (command not found, spawn failure, etc.)
                // This is still a policy-level failure but at a different layer.
                let error = crate::cloud::redaction::redact_secrets(&e.to_string());
                ctx.logger
                    .warn(&format!("Git push execution failed: {error}"));

                let ui = UIEvent::PushFailed {
                    remote: remote.clone(),
                    branch: branch.clone(),
                    error: error.clone(),
                };

                EffectResult::with_ui(
                    PipelineEvent::Commit(CommitEvent::PushFailed {
                        remote,
                        branch,
                        error,
                    }),
                    vec![ui],
                )
            }
        }
    }

    /// Create a pull request on the remote platform.
    ///
    /// Uses gh CLI for GitHub or glab CLI for GitLab.
    pub(super) fn handle_create_pull_request(
        ctx: &PhaseContext<'_>,
        base_branch: &str,
        head_branch: &str,
        title: &str,
        body: &str,
    ) -> EffectResult {
        ctx.logger
            .info(&format!("Creating PR: {head_branch} -> {base_branch}"));

        // Try gh CLI first (GitHub)
        let gh_result = ctx.executor.execute(
            "gh",
            &[
                "pr",
                "create",
                "--base",
                base_branch,
                "--head",
                head_branch,
                "--title",
                title,
                "--body",
                body,
            ],
            &[],
            Some(ctx.repo_root),
        );

        match gh_result {
            Ok(output) if is_success(&output) => {
                let url = output.stdout.trim().to_string();
                ctx.logger.info(&format!("Pull request created: {url}"));

                // Extract PR number from URL if possible
                let number = url
                    .rsplit('/')
                    .next()
                    .and_then(|s| s.parse::<u32>().ok())
                    .unwrap_or(0);

                let ui = UIEvent::PullRequestCreated {
                    url: url.clone(),
                    number,
                };

                EffectResult::with_ui(
                    PipelineEvent::Commit(CommitEvent::PullRequestCreated { url, number }),
                    vec![ui],
                )
            }
            Ok(output) => {
                let error = crate::cloud::redaction::redact_secrets(&output.stderr);
                ctx.logger.warn(&format!("PR creation failed: {error}"));

                let ui = UIEvent::PullRequestFailed {
                    error: error.clone(),
                };

                EffectResult::with_ui(
                    PipelineEvent::Commit(CommitEvent::PullRequestFailed { error }),
                    vec![ui],
                )
            }
            Err(e) => {
                // gh CLI not available, try glab (GitLab)
                ctx.logger
                    .info("gh CLI not available, trying glab for GitLab");

                let glab_result = ctx.executor.execute(
                    "glab",
                    &[
                        "mr",
                        "create",
                        "--target-branch",
                        base_branch,
                        "--source-branch",
                        head_branch,
                        "--title",
                        title,
                        "--description",
                        body,
                    ],
                    &[],
                    Some(ctx.repo_root),
                );

                match glab_result {
                    Ok(output) if is_success(&output) => {
                        let url = output.stdout.trim().to_string();
                        ctx.logger.info(&format!("Merge request created: {url}"));

                        let number = url
                            .rsplit('/')
                            .next()
                            .and_then(|s| s.parse::<u32>().ok())
                            .unwrap_or(0);

                        let ui = UIEvent::PullRequestCreated {
                            url: url.clone(),
                            number,
                        };

                        EffectResult::with_ui(
                            PipelineEvent::Commit(CommitEvent::PullRequestCreated { url, number }),
                            vec![ui],
                        )
                    }
                    Ok(output) => {
                        let error = crate::cloud::redaction::redact_secrets(&output.stderr);
                        ctx.logger.warn(&format!("MR creation failed: {error}"));
                        let ui = UIEvent::PullRequestFailed {
                            error: error.clone(),
                        };

                        EffectResult::with_ui(
                            PipelineEvent::Commit(CommitEvent::PullRequestFailed { error }),
                            vec![ui],
                        )
                    }
                    Err(e2) => {
                        let e = crate::cloud::redaction::redact_secrets(&e.to_string());
                        let e2 = crate::cloud::redaction::redact_secrets(&e2.to_string());
                        ctx.logger.warn(&format!(
                            "Neither gh nor glab CLI available: gh error: {e}, glab error: {e2}",
                        ));

                        let error =
                            format!("Neither gh nor glab CLI available (gh: {e}, glab: {e2})");
                        let ui = UIEvent::PullRequestFailed {
                            error: error.clone(),
                        };

                        EffectResult::with_ui(
                            PipelineEvent::Commit(CommitEvent::PullRequestFailed { error }),
                            vec![ui],
                        )
                    }
                }
            }
        }
    }
}

fn build_head_push_refspec(branch: &str) -> Option<String> {
    let trimmed = branch.trim();
    if trimmed.is_empty() {
        return None;
    }
    if trimmed.starts_with('-') {
        return None;
    }
    if trimmed.contains(':') {
        return None;
    }
    if trimmed.chars().any(|c| c.is_whitespace() || c == '\0') {
        return None;
    }

    let full_ref = if let Some(rest) = trimmed.strip_prefix("refs/heads/") {
        if rest.is_empty() {
            return None;
        }
        trimmed.to_string()
    } else if trimmed.starts_with("refs/") {
        // Only refs/heads/* is allowed from config; other ref namespaces are rejected.
        return None;
    } else {
        format!("refs/heads/{trimmed}")
    };

    Some(format!("HEAD:{full_ref}"))
}
